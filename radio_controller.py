"""
radio_controller.py
===================
Application-layer radio controller for BEKO central station (Raspberry Pi).
"""

import logging
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

_POST_TX_RX_SETTLE_S = 0.05


class _State:
    IDLE         = "IDLE"
    CMD_SENT     = "CMD_SENT"
    VERIFYING    = "VERIFYING"
    ACK_SENT     = "ACK_SENT"
    ALARM_ACTIVE = "ALARM_ACTIVE"
    RETRY        = "RETRY"


class RadioController:
    def __init__(self, mode: RadioMode = RadioMode.FSK,
                 aes_key: bytes = AES_KEY):
        self._mode   = mode
        self._crypto = CryptoLayer(aes_key)
        self._tx_seq = 0

        self._state       = _State.IDLE
        self._cmd_lock    = threading.Lock()
        self._status_lock = threading.Lock()

        self._status = {
            "state":        _State.IDLE,
            "actual_angle": None,
            "servo_status": None,
            "alarm_code":   None,
            "alarm_angle":  None,
            "link_ok":      False,
            "last_rx_ts":   None,
        }

        self._rx_event  = threading.Event()
        self._last_frame: Optional[bf.BekoFrame] = None

        self._handler: Optional[RadioHandler] = None

    def start(self):
        log.info("RadioController starting…")
        if not self._crypto.self_test():
            raise RuntimeError("Crypto self-test FAILED")
        self._handler = RadioHandler(self._mode, self._on_received)
        log.info("RadioController ready (FSK 868 MHz)")

    def stop(self):
        if self._handler:
            self._handler.cleanup()
        log.info("RadioController stopped")

    def send_cmd(self, angle: int, op_code: int = CMD_OP_ABSOLUTE) -> dict:
        with self._cmd_lock:
            last_error = None

            for attempt in range(CMD_MAX_RETRIES):
                if attempt > 0:
                    log.info(f"send_cmd: retry {attempt}/{CMD_MAX_RETRIES - 1}")

                self._rx_event.clear()
                self._last_frame = None

                try:
                    self._send_cmd_frame(angle, op_code)
                except Exception as e:
                    last_error = str(e)
                    log.error(f"send_cmd TX error: {e}")
                    break

                self._state = _State.CMD_SENT
                sleep(_POST_TX_RX_SETTLE_S)
                frame = self._wait_for_frame()

                if frame is None:
                    last_error = "Timeout: no TELEM received"
                    log.warning(last_error)
                    self._state = _State.RETRY
                    continue

                if frame.has_nak:
                    last_error = "NAK: STM32 rejected command"
                    log.warning(last_error)
                    self._send_ack_frame(result=1, flags=FRAME_FLAG_ACK)
                    self._state = _State.RETRY
                    continue

                if frame.type == FRAME_TYPE_ALARM:
                    alarm = bf.parse_alarm_payload(frame.plaintext)
                    last_error = (
                        f"ALARM received: code={alarm['alarm_code']} "
                        f"angle={alarm['angle_at_alarm']}"
                    )
                    log.warning(last_error)
                    self._state = _State.ALARM_ACTIVE
                    break

                telem = bf.parse_telem_payload(frame.plaintext)
                self._send_ack_frame(result=0, flags=FRAME_FLAG_ACK)
                self._state = _State.ACK_SENT

                with self._status_lock:
                    self._status.update({
                        "state":        _State.IDLE,
                        "actual_angle": telem["actual_angle"],
                        "servo_status": telem["servo_status"],
                        "link_ok":      True,
                        "last_rx_ts":   time(),
                    })

                self._state = _State.IDLE
                log.info(
                    f"CMD ok: actual_angle={telem['actual_angle']}° "
                    f"servo_status={telem['servo_status']} retries={attempt}"
                )
                return {
                    "ok":           True,
                    "actual_angle": telem["actual_angle"],
                    "servo_status": telem["servo_status"],
                    "retries":      attempt,
                    "error":        None,
                }

            self._state = _State.IDLE
            return {
                "ok":           False,
                "actual_angle": None,
                "servo_status": None,
                "retries":      attempt + 1,
                "error":        last_error,
            }

    def send_unlock(self) -> dict:
        with self._cmd_lock:
            try:
                self._rx_event.clear()
                self._last_frame = None
                self._send_ack_frame(result=0, flags=FRAME_FLAG_UNLOCK)
                self._state = _State.IDLE
                with self._status_lock:
                    self._status["alarm_code"]  = None
                    self._status["alarm_angle"] = None
                    self._status["state"]       = _State.IDLE
                return {"ok": True, "error": None}
            except Exception as e:
                log.error(f"send_unlock error: {e}")
                return {"ok": False, "error": str(e)}

    def send_lock(self) -> dict:
        with self._cmd_lock:
            try:
                self._send_ack_frame(result=0, flags=FRAME_FLAG_LOCK)
                return {"ok": True, "error": None}
            except Exception as e:
                log.error(f"send_lock error: {e}")
                return {"ok": False, "error": str(e)}

    def get_status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    def _send_cmd_frame(self, angle: int, op_code: int):
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
        log.info(f"TX CMD angle={angle} op={op_code} seq={self._tx_seq - 1}")
        self._handler.send(raw_frame.decode('latin-1'))

    def _send_ack_frame(self, result: int = 0, flags: int = FRAME_FLAG_ACK):
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
        log.info(f"TX ACK flags=0x{flags:02X} seq={self._tx_seq - 1}")
        self._handler.send(raw_frame.decode('latin-1'))

    def _wait_for_frame(self) -> Optional[bf.BekoFrame]:
        timeout_s = TELEM_TIMEOUT_MS / 1000.0
        received  = self._rx_event.wait(timeout=timeout_s)
        if not received:
            log.warning(f"_wait_for_frame: timeout after {timeout_s:.1f} s")
            return None
        return self._last_frame

    def _on_received(self, data: str, rssi=None, index=None):
        """
        Called by RadioHandler on every received FSK packet.
        data is a latin-1 str; convert back to bytes with ord().
        """
        raw = bytes(ord(c) for c in data)
        log.debug(f"RX {len(raw)} B  RSSI={rssi} dBm  #{index}")

        try:
            frame = bf.parse_frame(raw)
        except ValueError as e:
            log.warning(f"RX parse error: {e}  raw={raw.hex()}")
            return

        log.debug(f"parsed: type=0x{frame.type:02X} dst=0x{frame.dst:02X} src=0x{frame.src:02X} flags=0x{frame.flags:02X}")

        if frame.dst != ADDR_CENTRAL:
            log.debug(f"ignored: dst=0x{frame.dst:02X} != ADDR_CENTRAL=0x{ADDR_CENTRAL:02X}")
            return

        try:
            frame.plaintext = self._crypto.decrypt(frame.data)
        except ValueError as e:
            log.warning(f"RX crypto error: {e}")
            return

        log.info(f"RX {frame}  RSSI={rssi} dBm")

        if frame.type == FRAME_TYPE_TELEM:
            telem = bf.parse_telem_payload(frame.plaintext)
            log.info(
                f"  TELEM: servo_status={telem['servo_status']}  "
                f"actual_angle={telem['actual_angle']}°"
            )
            with self._status_lock:
                self._status.update({
                    "actual_angle": telem["actual_angle"],
                    "servo_status": telem["servo_status"],
                    "link_ok":      True,
                    "last_rx_ts":   time(),
                })
            self._last_frame = frame
            self._rx_event.set()

        elif frame.type == FRAME_TYPE_ALARM:
            alarm = bf.parse_alarm_payload(frame.plaintext)
            code_name = ALARM_CODE_NAMES.get(
                alarm["alarm_code"], f"0x{alarm['alarm_code']:02X}"
            )
            log.warning(
                f"  ALARM: code={code_name}  "
                f"angle_at_alarm={alarm['angle_at_alarm']}°  "
                f"failsafe={frame.has_failsafe}"
            )
            with self._status_lock:
                self._status.update({
                    "state":       _State.ALARM_ACTIVE,
                    "alarm_code":  alarm["alarm_code"],
                    "alarm_angle": alarm["angle_at_alarm"],
                    "last_rx_ts":  time(),
                })
            self._last_frame = frame
            self._rx_event.set()

        elif frame.type == FRAME_TYPE_ACK:
            log.debug("RX ACK from STM32 (unexpected)")

        else:
            log.warning(f"RX unknown type 0x{frame.type:02X}")