"""
main.py
=======
Entry point for the BEKO central-station radio layer.

This file wires RadioController (protocol logic) to the radio hardware
and exposes a minimal Flask API so Nginx can proxy commands from the
operator GUI.

Flask endpoints (JSON in/out, all POST):
    /api/cmd          — send azimuth command
    /api/unlock       — release locked node
    /api/lock         — emergency lock
    /api/status       — get current system status (GET)

Run with:
    python3 main.py

The Flask server binds to 127.0.0.1:5000 (Nginx reverse-proxies it).
"""

import logging
import sys
from flask import Flask, request, jsonify

from radio_handle import RadioMode
from radio_controller import RadioController
from beko_protocol import CMD_OP_ABSOLUTE, CMD_OP_RELATIVE

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/home/centrala/rotor/logs/radio.log"),
    ],
)
log = logging.getLogger("beko.main")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

app        = Flask(__name__)
controller = RadioController(mode=RadioMode.FSK)

# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/api/cmd", methods=["POST"])
def api_cmd():
    """
    Send azimuth command to STM32.

    JSON body:
        {
          "angle":   <int 0–359>,
          "op_code": <int 1=absolute | 2=relative>   (optional, default 1)
        }

    Response:
        {
          "ok":           <bool>,
          "actual_angle": <int or null>,
          "servo_status": <int or null>,
          "retries":      <int>,
          "error":        <str or null>
        }
    """
    body = request.get_json(force=True, silent=True) or {}

    angle   = body.get("angle")
    op_code = body.get("op_code", CMD_OP_ABSOLUTE)

    if angle is None or not isinstance(angle, int):
        return jsonify({"ok": False, "error": "angle (int) required"}), 400
    if angle < 0 or angle > 359:
        return jsonify({"ok": False, "error": "angle must be 0–359"}), 400
    if op_code not in (CMD_OP_ABSOLUTE, CMD_OP_RELATIVE):
        return jsonify({"ok": False, "error": "op_code must be 1 or 2"}), 400

    result = controller.send_cmd(angle, op_code)
    status = 200 if result["ok"] else 500
    return jsonify(result), status


@app.route("/api/unlock", methods=["POST"])
def api_unlock():
    """Release a locked/alarmed STM32 node."""
    result = controller.send_unlock()
    status = 200 if result["ok"] else 500
    return jsonify(result), status


@app.route("/api/lock", methods=["POST"])
def api_lock():
    """Emergency lock — immediately stops rotor movement."""
    result = controller.send_lock()
    status = 200 if result["ok"] else 500
    return jsonify(result), status


@app.route("/api/status", methods=["GET"])
def api_status():
    """Return current system status."""
    return jsonify(controller.get_status()), 200


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("BEKO central station starting…")
    controller.start()
    try:
        # Bind to localhost only — Nginx handles external access
        app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        log.info("Shutting down…")
    finally:
        controller.stop()
