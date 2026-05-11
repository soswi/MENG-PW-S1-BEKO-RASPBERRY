"""
radio_controller.py
===================
Application-layer radio controller for BEKO central station (Raspberry Pi).

This module sits between radio_handle.py (hardware FSK driver) and the
Flask/Nginx operator interface.  It owns the protocol state machine,
sequence counters, and all frame build/parse logic.

Architecture:
    Flask API  ←→  RadioController  ←→  RadioHandler (radio_handle.py)
                         ↕
                    CryptoLayer
                    beko_frame

Public interface (called from Flask):
    controller.send_cmd(angle, op_code)   → transaction result dict
    controller.send_unlock()              → dict
    controller.send_lock()                → dict
    controller.get_status()               → last known status dict
    controller.start() / controller.stop()

RadioController is a singleton — create once at Flask app startup.

Thread safety:
    _cmd_lock ensures only one transaction runs at a time.
    Status dict is updated from the RX callback thread; reads are
    protected by _status_lock.
"""

import logging
import threading
from time import sleep, time
from typing import Optional

from radio_handle import RadioHandler, RadioMode
from crypto_layer import CryptoLayer
import beko_frame as bf
from beko_protocol import (
    # addresses
    ADDR_CENTRAL, ADDR_NODE1,
    # frame types
    FRAME_TYPE_CMD, FRAME_TYPE_TELEM, FRAME_TYPE_ALARM, FRAME_TYPE_ACK,
    # flags
    FRAME_FLAG_ACK_REQ, FRAME_FLAG_ACK, FRAME_FLAG_NAK,
    FRAME_FLAG_UNLOCK, FRAME_FLAG_LOCK,
    # op-codes and status codes
    CMD_OP_ABSOLUTE, CMD_OP_RELATIVE,
    SERVO_STATUS_OK, SERVO_STATUS_ANGLE_ERROR, SERVO_STATUS_ROTOR_STOP,
    ALARM_CODE_NAMES,
    # timing
    TELEM_TIMEOUT_MS, CMD_MAX_RETRIES, AES_KEY,
)

log = logging.getLogger("beko.radio")


# ---------------------------------------------------------------------------
# RPi protocol state machine states (mirrors section 10 of the reference)
# ---------------------------------------------------------------------------

class _State:
    IDLE         = "IDLE"
    CMD_SENT     = "CMD_SENT"
    VERIFYING    = "VERIFYING"
    ACK_SENT     = "ACK_SENT"
    ALARM_ACTIVE = "ALARM_ACTIVE"
    RETRY        = "RETRY"


class RadioController:
    """
    BEKO central-station radio protocol controller.

    Usage::

        controller = RadioController()
        controller.start()

        # from Flask handler:
        result = controller.send_cmd(90, CMD_OP_ABSOLUTE)
    """

    def __init__(self, mode: RadioMode = RadioMode.FSK,
                 aes_key: bytes = AES_KEY):
        self._mode   = mode
        self._crypto = CryptoLayer(aes_key)

        # Tx sequence counter — incremented by CryptoLayer on each encrypt()
        # rx_counter_last is owned by CryptoLayer
        self._tx_seq: int = 0     # frame-header counter (16-bit, wraps at 0xFFFF)

        # State
        self._state        = _State.IDLE
        self._cmd_lock     = threading.Lock()   # one transaction at a time
        self._status_lock  = threading.Lock()

        # Last known status — updated on every TELEM / ALARM received
        self._status = {
            "state":        _State.IDLE,
            "actual_angle": None,
            "servo_status": None,
            "alarm_code":   None,
            "alarm_angle":  None,
            "link_ok":      False,
            "last_rx_ts":   None,
        }

        # Event used to signal an inbound TELEM/ACK to the waiting TX thread
        self._telem_event = threading.Event()
        self._last_frame: Optional[bf.BekoFrame] = None

        self._handler: Optional[RadioHandler] = None
        self._running = False

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def start(self):
        """Initialise hardware and run crypto self-test."""
        log.info("RadioController starting…")

        # Crypto self-test — hard fail on startup if this fails
        if not self._crypto.self_test():
            raise RuntimeError("Crypto self-test FAILED — aborting startup")

        self._handler = RadioHandler(self._mode, self._on_received)
        self._running = True
        log.info("RadioController ready (FSK 868 MHz)")

    def stop(self):
        """Clean up radio hardware."""
        self._running = False
        if self._handler:
            self._handler.cleanup()
        log.info("RadioController stopped")

    # ------------------------------------------------------------------ #
    # Public command API                                                  #
    # ------------------------------------------------------------------ #

    def send_cmd(self, angle: int, op_code: int = CMD_OP_ABSOLUTE) -> dict:
        """
        Send a CMD_AZIMUTH frame to STM32 and wait for TELEM response.

        Full transaction per section 9.1 / 9.2 of the protocol reference:
            1. Send FRAME_TYPE_CMD (flags=ACK_REQ)
            2. Wait up to TELEM_TIMEOUT_MS for FRAME_TYPE_TELEM (flags=ACK_REQ)
            3. Send FRAME_TYPE_ACK (flags=ACK)

        Retries up to CMD_MAX_RETRIES on NAK or timeout.

        Args:
            angle:   0–359°
            op_code: CMD_OP_ABSOLUTE or CMD_OP_RELATIVE

        Returns:
            dict: {
                "ok":           bool,
                "actual_angle": int or None,
                "servo_status": int or None,
                "retries":      int,
                "error":        str or None,
            }
        """
        with self._cmd_lock:
            last_error = None
            for attempt in range(CMD_MAX_RETRIES):
                if attempt > 0:
                    log.info(f"send_cmd: retry {attempt}/{CMD_MAX_RETRIES - 1}")

                # Build and send CMD
                try:
                    self._send_cmd_frame(angle, op_code)
                except Exception as e:
                    last_error = str(e)
                    log.error(f"send_cmd TX error: {e}")
                    break

                self._state = _State.CMD_SENT

                # Wait for TELEM
                frame = self._wait_for_telem()

                if frame is None:
                    last_error = "Timeout: no TELEM received"
                    log.warning(last_error)
                    self._state = _State.RETRY
                    continue

                # Check for NAK
                if frame.has_nak:
                    last_error = "NAK: STM32 rejected command"
                    log.warning(last_error)
                    # Acknowledge the NAK
                    self._send_ack_frame(result=1, flags=FRAME_FLAG_ACK)
                    self._state = _State.RETRY
                    continue

                # Parse TELEM payload
                if frame.plaintext:
                    telem = bf.parse_telem_payload(frame.plaintext)
                else:
                    telem = {"servo_status": None, "actual_angle": None}

                # Send ACK
                self._send_ack_frame(result=0, flags=FRAME_FLAG_ACK)
                self._state = _State.ACK_SENT

                # Update status
                with self._status_lock:
                    self._status.update({
                        "state":        _State.IDLE,
                        "actual_angle": telem.get("actual_angle"),
                        "servo_status": telem.get("servo_status"),
                        "link_ok":      True,
                        "last_rx_ts":   time(),
                    })

                self._state = _State.IDLE
                return {
                    "ok":           True,
                    "actual_angle": telem.get("actual_angle"),
                    "servo_status": telem.get("servo_status"),
                    "retries":      attempt,
                    "error":        None,
                }

            # All retries exhausted
            self._state = _State.IDLE
            return {
                "ok":           False,
                "actual_angle": None,
                "servo_status": None,
                "retries":      CMD_MAX_RETRIES,
                "error":        last_error,
            }

    def send_unlock(self) -> dict:
        """
        Send FRAME_TYPE_ACK with FRAME_FLAG_UNLOCK to release a locked node.
        Called from GUI after operator acknowledges alarm.
        """
        with self._cmd_lock:
            try:
                self._send_ack_frame(result=0, flags=FRAME_FLAG_UNLOCK)
                self._state = _State.IDLE
                with self._status_lock:
                    self._status["alarm_code"]  = None
                    self._status["alarm_angle"] = None
                return {"ok": True, "error": None}
            except Exception as e:
                log.error(f"send_unlock error: {e}")
                return {"ok": False, "error": str(e)}

    def send_lock(self) -> dict:
        """
        Send FRAME_TYPE_ACK with FRAME_FLAG_LOCK (emergency lock from operator).
        """
        with self._cmd_lock:
            try:
                self._send_ack_frame(result=0, flags=FRAME_FLAG_LOCK)
                return {"ok": True, "error": None}
            except Exception as e:
                log.error(f"send_lock error: {e}")
                return {"ok": False, "error": str(e)}

    def get_status(self) -> dict:
        """Return a copy of the current status dict (thread-safe)."""
        with self._status_lock:
            return dict(self._status)

    # ------------------------------------------------------------------ #
    # Internal frame builders                                             #
    # ------------------------------------------------------------------ #

    def _send_cmd_frame(self, angle: int, op_code: int):
        """Build, encrypt, and transmit a CMD frame."""
        payload   = bf.build_cmd_payload(op_code, angle)
        enc       = self._crypto.encrypt(payload)
        raw_frame = bf.build_frame(
            frame_type=FRAME_TYPE_CMD,
            counter=self._tx_seq & 0xFFFF,
            flags=FRAME_FLAG_ACK_REQ,
            encrypted_payload=enc,
            dst=ADDR_NODE1,
            src=ADDR_CENTRAL,
        )
        self._tx_seq += 1
        log.debug(f"TX CMD angle={angle} op={op_code} seq={self._tx_seq - 1}")
        self._handler.send(raw_frame.decode('latin-1'))

    def _send_ack_frame(self, result: int = 0, flags: int = FRAME_FLAG_ACK):
        """Build, encrypt, and transmit an ACK/UNLOCK/LOCK frame."""
        payload   = bf.build_ack_payload(result)
        enc       = self._crypto.encrypt(payload)
        raw_frame = bf.build_frame(
            frame_type=FRAME_TYPE_ACK,
            counter=self._tx_seq & 0xFFFF,
            flags=flags,
            encrypted_payload=enc,
            dst=ADDR_NODE1,
            src=ADDR_CENTRAL,
        )
        self._tx_seq += 1
        log.debug(f"TX ACK flags=0x{flags:02X} seq={self._tx_seq - 1}")
        self._handler.send(raw_frame.decode('latin-1'))

    # ------------------------------------------------------------------ #
    # TELEM wait                                                          #
    # ------------------------------------------------------------------ #

    def _wait_for_telem(self) -> Optional[bf.BekoFrame]:
        """
        Block until a TELEM or ALARM frame arrives from STM32, or timeout.

        Returns the frame on success, None on timeout.
        """
        self._telem_event.clear()
        self._last_frame = None
        timeout_s = TELEM_TIMEOUT_MS / 1000.0

        received = self._telem_event.wait(timeout=timeout_s)
        if not received:
            return None
        return self._last_frame

    # ------------------------------------------------------------------ #
    # RX callback — called from radio_handle thread                       #
    # ------------------------------------------------------------------ #

    def _on_received(self, data: str, rssi=None, index=None):
        """
        Handle a raw received frame from RadioHandler.

        data is a latin-1 encoded str (radio_handle converts bytes→str
        via chr(); we reverse with ord()).
        """
        raw = bytes(ord(c) for c in data)
        log.debug(f"RX {len(raw)} B  RSSI={rssi} dBm  #{index}")

        # Parse wire frame
        try:
            frame = bf.parse_frame(raw)
        except ValueError as e:
            log.warning(f"RX frame parse error: {e}")
            return

        # Address filter — only accept frames addressed to us
        if frame.dst != ADDR_CENTRAL:
            log.debug(f"RX ignored: dst=0x{frame.dst:02X} (not ours)")
            return

        # Decrypt payload
        try:
            frame.plaintext = self._crypto.decrypt(frame.data)
        except ValueError as e:
            log.warning(f"RX crypto error: {e}")
            return

        log.info(
            f"RX {frame} RSSI={rssi} "
            f"plaintext={frame.plaintext.hex() if frame.plaintext else 'None'}"
        )

        # Handle by frame type
        if frame.type == FRAME_TYPE_TELEM:
            self._handle_telem(frame)

        elif frame.type == FRAME_TYPE_ALARM:
            self._handle_alarm(frame)

        elif frame.type == FRAME_TYPE_ACK:
            # STM32 should not be sending ACK frames to us, but handle gracefully
            log.debug("RX ACK from STM32 (unexpected in normal flow)")

        else:
            log.warning(f"RX unknown frame type 0x{frame.type:02X}")

    def _handle_telem(self, frame: bf.BekoFrame):
        telem = bf.parse_telem_payload(frame.plaintext)
        log.info(
            f"TELEM: servo_status={telem['servo_status']} "
            f"actual_angle={telem['actual_angle']}°"
        )
        with self._status_lock:
            self._status.update({
                "actual_angle": telem["actual_angle"],
                "servo_status": telem["servo_status"],
                "link_ok":      True,
                "last_rx_ts":   time(),
            })
        # Signal the waiting send_cmd() call
        self._last_frame = frame
        self._telem_event.set()

    def _handle_alarm(self, frame: bf.BekoFrame):
        alarm = bf.parse_alarm_payload(frame.plaintext)
        code_name = ALARM_CODE_NAMES.get(alarm["alarm_code"], f"0x{alarm['alarm_code']:02X}")
        log.warning(
            f"ALARM: code={code_name} angle_at_alarm={alarm['angle_at_alarm']}° "
            f"failsafe={frame.has_failsafe}"
        )
        with self._status_lock:
            self._status.update({
                "state":        _State.ALARM_ACTIVE,
                "alarm_code":   alarm["alarm_code"],
                "alarm_angle":  alarm["angle_at_alarm"],
                "last_rx_ts":   time(),
            })
        # Wake any waiting transaction so it can react to the alarm
        self._last_frame = frame
        self._telem_event.set()
