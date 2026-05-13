"""
app.py
======
Flask backend for the BEKO antenna rotator GUI.
Bridges the browser UI to RadioController (FSK 868 MHz protocol layer).

Run:
    cd /home/centrala/MENG-PW-S1-BEKO-WEB/rotor/backend
    python3 app.py

Endpoints:
    GET  /                       → serve index.html
    GET  /api/status?after=<id>  → state + new events since <id>
    POST /api/command            → send azimuth command (blocks until TELEM)
    POST /api/stop               → emergency LOCK
    POST /api/unlock             → send UNLOCK and wait for TELEM/ALARM
    POST /api/alarm/clear        → alias for /api/unlock
    POST /api/restart            → restart the beko-web systemd service
"""

import os
import sys
import logging
import signal
import subprocess
import collections
import threading

# Radio layer lives in a separate repo — add it to path explicitly.
_RADIO_ROOT = "/home/centrala/rfm95/MENG-PW-S1-BEKO-RASPBERRY"
sys.path.insert(0, _RADIO_ROOT)

# Web repo root (parent of backend/) — used only for the logs directory.
_WEB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, render_template
from radio_controller import RadioController, RadioMode
from beko_protocol import CMD_OP_ABSOLUTE, ALARM_CODE_NAMES

# ── Logging ──────────────────────────────────────────────────────────────────
_LOGS_DIR = os.path.join(_WEB_ROOT, "logs")
os.makedirs(_LOGS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(_LOGS_DIR, "events.log")),
    ],
)
logger = logging.getLogger(__name__)

# ── Flask + RadioController ───────────────────────────────────────────────────
app = Flask(__name__)
controller = RadioController(mode=RadioMode.FSK)

# ── Event queue (browser polls /api/status?after=<id> to get new entries) ────
_events     = collections.deque(maxlen=200)
_event_lock = threading.Lock()
_event_seq  = 0
_prev_alarm = False

_SERVO_STATUS = {0: "OK", 1: "ANGLE_ERR", 2: "ROTOR_STOP"}


def _evt(kind: str, msg: str):
    """Append a timestamped event visible in the browser log panel."""
    global _event_seq
    with _event_lock:
        _event_seq += 1
        _events.append({"id": _event_seq, "kind": kind, "msg": msg})


def _events_after(after_id: int) -> list:
    with _event_lock:
        return [e for e in _events if e["id"] > after_id]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def get_status():
    """
    Non-blocking — called every 500 ms by the browser.
    Optional query param: after=<last_event_id>  (default 0)
    Returns current state + any new events since that id.
    """
    global _prev_alarm

    st = controller.get_status()
    is_alarm = st["state"] == "ALARM_ACTIVE"

    # Detect unsolicited ALARM (STM32 sent it between commands).
    # Only the first thread to see the transition adds the event.
    add_alarm = False
    with _event_lock:
        if is_alarm and not _prev_alarm:
            _prev_alarm = True
            add_alarm   = True
        elif not is_alarm:
            _prev_alarm = False

    if add_alarm:
        alarm_name = ALARM_CODE_NAMES.get(st["alarm_code"], f"0x{st['alarm_code']:02X}")
        _evt("alarm", f"ALARM ← {alarm_name} @ {st.get('alarm_angle', '?')}°")

    alarm_msg = (
        ALARM_CODE_NAMES.get(st["alarm_code"], f"0x{st['alarm_code']:02X}")
        if st["alarm_code"] is not None else ""
    )

    after = request.args.get("after", 0, type=int)
    return jsonify({
        "state":        st["state"],
        "actual_angle": st["actual_angle"],
        "servo_status": st["servo_status"],
        "alarm":        is_alarm,
        "alarm_code":   st["alarm_code"],
        "alarm_msg":    alarm_msg,
        "alarm_angle":  st["alarm_angle"],
        "link_ok":      st["link_ok"],
        "last_rx_ts":   st["last_rx_ts"],
        "events":       _events_after(after),
    })


@app.route("/api/command", methods=["POST"])
def post_command():
    """
    Send an azimuth command and wait for TELEM (blocks up to TELEM_TIMEOUT_MS).
    Flask threaded=True keeps /api/status polls alive during this wait.

    Body: { "azimuth": <int 0-359>, "op_code": <int, optional, default 1> }
    """
    body = request.get_json(silent=True)
    if not body or "azimuth" not in body:
        return jsonify({"ok": False, "error": "Missing 'azimuth'"}), 400

    try:
        azimuth = int(body["azimuth"])
        op_code = int(body.get("op_code", CMD_OP_ABSOLUTE))
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if not (0 <= azimuth <= 359):
        return jsonify({"ok": False, "error": "Azimuth out of range 0-359"}), 400

    _evt("ok", f"CMD → {azimuth}°")
    result = controller.send_cmd(azimuth, op_code)

    if result["ok"]:
        srv = _SERVO_STATUS.get(result["servo_status"], str(result["servo_status"]))
        _evt("ok", f"TELEM ← {result['actual_angle']}°  srv={srv}")
    else:
        error = result.get("error", "unknown")
        if "ALARM" in error:
            st = controller.get_status()
            alarm_name = ALARM_CODE_NAMES.get(st["alarm_code"], f"0x{st['alarm_code']:02X}")
            _evt("alarm", f"ALARM ← {alarm_name} @ {st.get('alarm_angle', '?')}°")
        else:
            _evt("warn", f"ERR: {error}")

    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/stop", methods=["POST"])
def post_stop():
    """Emergency LOCK — send immediately, no response expected."""
    _evt("alarm", "LOCK → emergency stop")
    result = controller.send_lock()
    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/restart", methods=["POST"])
def post_restart():
    """
    Restart the beko-web systemd service.
    Responds immediately; actual restart happens 1 s later so the response
    has time to reach the browser before the process dies.
    Requires: sudo systemctl restart beko-web  (passwordless via /etc/sudoers.d/beko-web)
    """
    logger.info("Restart requested via web UI")
    _evt("warn", "RESTART → serwis zostanie uruchomiony ponownie…")

    def _do_restart():
        import time
        time.sleep(1)
        subprocess.Popen(["sudo", "systemctl", "restart", "beko-web"])

    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/unlock", methods=["POST"])
@app.route("/api/alarm/clear", methods=["POST"])
def post_unlock():
    """Send UNLOCK and wait for TELEM/ALARM response."""
    _evt("warn", "UNLOCK →")
    result = controller.send_unlock()
    if result["ok"]:
        st = controller.get_status()
        angle = st.get("actual_angle", "?")
        _evt("ok", f"UNLOCK OK ← {angle}°")
    else:
        _evt("alarm", f"UNLOCK FAIL: {result.get('error', '?')}")
    return jsonify(result), (200 if result["ok"] else 500)


# ── Shutdown ──────────────────────────────────────────────────────────────────

def _shutdown(sig, frame):
    logger.info("Shutting down RadioController...")
    controller.stop()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("╔════════════════════════════════════════════════════════╗")
    logger.info("║     SYSTEM BEKO — Backend Rotor Antenowy               ║")
    logger.info("╚════════════════════════════════════════════════════════╝")
    logger.info(f"Radio root: {_RADIO_ROOT}")
    logger.info(f"Web root  : {_WEB_ROOT}")
    logger.info(f"Log file  : {os.path.join(_LOGS_DIR, 'events.log')}")

    logger.info("Starting RadioController (FSK 868 MHz)...")
    controller.start()

    logger.info("Flask starting on 127.0.0.1:5000  (threaded)")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
