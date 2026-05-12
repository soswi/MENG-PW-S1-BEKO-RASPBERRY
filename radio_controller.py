"""
radio_controller.py
===================
Application-layer radio controller for BEKO central station (Raspberry Pi).
...
"""

import logging
import os
import threading
from time import sleep, time
from typing import Optional

from radio_handle import RadioHandler, RadioMode
from crypto_layer import CryptoLayer
import beko_frame as bf
from beko_protocol import (
    ADDR_CENTRAL, ADDR_NODE1,
    FRAME_TYPE_CMD, FRAME_TYPE_TELEM, FRAME_TYPE_ALARM, FRAME_TYPE_ACK,
    FRAME_FLAG_ACK_REQ, FRAME_FLAG_ACK, FRAME_FLAG_NAK,
    FRAME_FLAG_UNLOCK, FRAME_FLAG_LOCK,
    CMD_OP_ABSOLUTE, CMD_OP_RELATIVE,
    ALARM_CODE_NAMES,
    TELEM_TIMEOUT_MS, CMD_MAX_RETRIES, AES_KEY,
)

log = logging.getLogger("beko.radio")

# Persist TX sequence counter across restarts so STM32 replay window never rejects.
_SEQ_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".beko_tx_seq")

_POST_TX_RX_SETTLE_S = 0.05


class _State:
    IDLE         = "IDLE"
    CMD_SENT     = "CMD_SENT"
    VERIFYING    = "VERIFYING"
    ACK_SENT     = "ACK_SENT"
    ALARM_ACTIVE = "ALARM_ACTIVE"
    RETRY        = "RETRY"


class RadioController:

    def __init__(self, mode: RadioMode = RadioMode.FSK, aes_key: bytes = AES_KEY):
        self._mode   = mode
        self._crypto = CryptoLayer(aes_key)
        self._tx_seq = self._load_tx_seq()   # <-- was hardcoded 0

        self._state       = _State.IDLE
        self._cmd_lock    = threading.Lock()
        self._status_lock = threading.Lock()
        self._status = { ... }
        self._rx_event  = threading.Event()
        self._last_frame: Optional[bf.BekoFrame] = None
        self._handler: Optional[RadioHandler] = None

    # --- NEW: seq counter persistence ---

    def _load_tx_seq(self) -> int:
        try:
            with open(_SEQ_FILE, "r") as f:
                saved = int(f.read().strip())
            seq = (saved + 20) & 0xFFFF
            log.info(f"TX seq loaded: saved={saved} → starting at {seq}")
            return seq
        except Exception:
            log.info("TX seq file not found — starting at 1")
            return 1

    def _save_tx_seq(self):
        try:
            with open(_SEQ_FILE, "w") as f:
                f.write(str(self._tx_seq))
        except Exception as e:
            log.warning(f"Could not save TX seq: {e}")

    # ... (rest unchanged) ...

    def _send_cmd_frame(self, angle, op_code):
        ...
        self._tx_seq += 1
        log.info(f"TX CMD angle={angle} op={op_code} seq={self._tx_seq - 1}")
        self._save_tx_seq()   # <-- NEW
        self._handler.send(raw_frame.decode('latin-1'))

    def _send_ack_frame(self, result=0, flags=FRAME_FLAG_ACK):
        ...
        self._tx_seq += 1
        log.info(f"TX ACK flags=0x{flags:02X} seq={self._tx_seq - 1}")
        self._save_tx_seq()   # <-- NEW
        self._handler.send(raw_frame.decode('latin-1'))