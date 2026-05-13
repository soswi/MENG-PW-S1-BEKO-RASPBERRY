"""
app.py
======
Flask backend for the BEKO antenna rotator GUI.
Bridges the browser UI to RadioController (FSK 868 MHz protocol layer).

Run:
    cd /home/centrala/MENG-PW-S1-BEKO-WEB/rotor/backend
    python3 app.py

Endpoints:
    GET  /                  → serve index.html
    GET  /api/status        → current controller state (fast, non-blocking)
    POST /api/command       → send azimuth command (blocks until TELEM arrives)
    POST /api/stop          → emergency LOCK
    POST /api/unlock        → send UNLOCK and wait for TELEM/ALARM response
    POST /api/alarm/clear   → alias for /api/unlock
"""

import os
import sys
import logging
import signal

# Add the repo root to path so radio_controller and friends are importable
# regardless of where the user cd's from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from flask import Flask, jsonify, request, render_template
from radio_controller import RadioController, RadioMode
from beko_protocol import CMD_OP_ABSOLUTE, ALARM_CODE_NAMES

# ── Logging ──────────────────────────────────────────────────────────────────
_LOGS_DIR = os.path.join(_REPO_ROOT, "logs")
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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def get_status():
    """Non-blocking — called every 500 ms by the browser."""
    st = controller.get_status()
    alarm_msg = (
        ALARM_CODE_NAMES.get(st["alarm_code"], f"0x{st['alarm_code']:02X}")
        if st["alarm_code"] is not None
        else ""
    )
    return jsonify({
        "state":        st["state"],
        "actual_angle": st["actual_angle"],
        "servo_status": st["servo_status"],
        "alarm":        st["state"] == "ALARM_ACTIVE",
        "alarm_code":   st["alarm_code"],
        "alarm_msg":    alarm_msg,
        "alarm_angle":  st["alarm_angle"],
        "link_ok":      st["link_ok"],
        "last_rx_ts":   st["last_rx_ts"],
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

    result = controller.send_cmd(azimuth, op_code)
    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/stop", methods=["POST"])
def post_stop():
    """Emergency LOCK — send immediately, no response expected."""
    result = controller.send_lock()
    return jsonify(result), (200 if result["ok"] else 500)


@app.route("/api/unlock", methods=["POST"])
@app.route("/api/alarm/clear", methods=["POST"])
def post_unlock():
    """Send UNLOCK and wait for TELEM/ALARM response."""
    result = controller.send_unlock()
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
    logger.info(f"Repo root : {_REPO_ROOT}")
    logger.info(f"Log file  : {os.path.join(_LOGS_DIR, 'events.log')}")

    logger.info("Starting RadioController (FSK 868 MHz)...")
    controller.start()

    logger.info("Flask starting on 127.0.0.1:5000  (threaded)")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
