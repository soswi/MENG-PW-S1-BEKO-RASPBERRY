"""
beko_frame.py
=============
Serialization and deserialization of BEKO radio protocol frames.

Wire format (41 bytes, all multi-byte integers big-endian):

  Offset  Size  Field      Description
  ------  ----  ---------  ------------------------------------------
  0       1 B   type       Frame type (FRAME_TYPE_*)
  1       2 B   counter    Sequence counter, big-endian uint16
  3       1 B   flags      Flag bits (FRAME_FLAG_* OR-ed together)
  4      32 B   data       Encrypted payload: IV(16) + CT(12) + MIC(4)
  36      1 B   data_len   Number of valid bytes in data[] — always 32
  37      2 B   crc        CRC-16/CCITT over bytes 0–36
  39      1 B   dst        Destination node address
  40      1 B   src        Source node address

CRC covers bytes 0–36 (does NOT include dst/src).
Everything is big-endian (matching STM32 implementation).
"""

import struct
from dataclasses import dataclass
from typing import Optional

from beko_protocol import (
    FRAME_SIZE, ENCRYPTED_SIZE,
    FRAME_TYPE_NAMES, FRAME_FLAG_ACK_REQ, FRAME_FLAG_ACK,
    FRAME_FLAG_ALARM, FRAME_FLAG_FAILSAFE, FRAME_FLAG_NAK,
    FRAME_FLAG_UNLOCK, FRAME_FLAG_LOCK,
)


# ---------------------------------------------------------------------------
# CRC-16/CCITT
# ---------------------------------------------------------------------------

def crc16_ccitt(data: bytes) -> int:
    """
    CRC-16/CCITT: polynomial 0x1021, initial value 0xFFFF.
    Computed over bytes 0–36 (before the crc field).
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# Frame dataclass
# ---------------------------------------------------------------------------

@dataclass
class BekoFrame:
    """Parsed representation of a BEKO protocol frame."""
    type:     int          # FRAME_TYPE_*
    counter:  int          # 16-bit sequence counter
    flags:    int          # OR-ed FRAME_FLAG_* bits
    data:     bytes        # 32 bytes: encrypted payload
    data_len: int          # always 32
    crc:      int          # received CRC (for display/debug)
    dst:      int          # destination address
    src:      int          # source address

    # Optional — populated after successful crypto.decrypt()
    plaintext: Optional[bytes] = None

    # ------------------------------------------------------------------ #
    # Convenience flag accessors                                          #
    # ------------------------------------------------------------------ #

    @property
    def has_ack_req(self)  -> bool: return bool(self.flags & FRAME_FLAG_ACK_REQ)
    @property
    def has_ack(self)      -> bool: return bool(self.flags & FRAME_FLAG_ACK)
    @property
    def has_alarm(self)    -> bool: return bool(self.flags & FRAME_FLAG_ALARM)
    @property
    def has_failsafe(self) -> bool: return bool(self.flags & FRAME_FLAG_FAILSAFE)
    @property
    def has_nak(self)      -> bool: return bool(self.flags & FRAME_FLAG_NAK)
    @property
    def has_unlock(self)   -> bool: return bool(self.flags & FRAME_FLAG_UNLOCK)
    @property
    def has_lock(self)     -> bool: return bool(self.flags & FRAME_FLAG_LOCK)

    def type_name(self) -> str:
        return FRAME_TYPE_NAMES.get(self.type, f"0x{self.type:02X}")

    def flags_str(self) -> str:
        names = []
        if self.has_ack_req:  names.append("ACK_REQ")
        if self.has_ack:      names.append("ACK")
        if self.has_alarm:    names.append("ALARM")
        if self.has_failsafe: names.append("FAILSAFE")
        if self.has_nak:      names.append("NAK")
        if self.has_unlock:   names.append("UNLOCK")
        if self.has_lock:     names.append("LOCK")
        return "|".join(names) if names else "0"

    def __str__(self) -> str:
        return (
            f"BekoFrame(type={self.type_name()} ctr={self.counter} "
            f"flags=[{self.flags_str()}] dst=0x{self.dst:02X} src=0x{self.src:02X})"
        )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_frame(frame_type: int,
                counter: int,
                flags: int,
                encrypted_payload: bytes,
                dst: int,
                src: int) -> bytes:
    """
    Build a complete 41-byte BEKO wire frame.

    Args:
        frame_type:          FRAME_TYPE_* constant
        counter:             16-bit tx sequence counter (big-endian on wire)
        flags:               OR-ed FRAME_FLAG_* bits
        encrypted_payload:   exactly 32 bytes (IV + CT + MIC from CryptoLayer)
        dst:                 destination node address (ADDR_*)
        src:                 source node address (ADDR_*)

    Returns:
        bytes: 41-byte frame ready for radio_handler.send()

    Raises:
        ValueError: if encrypted_payload is not exactly 32 bytes
    """
    if len(encrypted_payload) != ENCRYPTED_SIZE:
        raise ValueError(
            f"encrypted_payload must be exactly {ENCRYPTED_SIZE} bytes, "
            f"got {len(encrypted_payload)}"
        )

    # Bytes 0–36: type(1) + counter(2) + flags(1) + data(32) + data_len(1)
    pre_crc = struct.pack('>BHB', frame_type, counter, flags)
    pre_crc += encrypted_payload                   # 32 bytes
    pre_crc += struct.pack('>B', ENCRYPTED_SIZE)   # data_len = 32

    assert len(pre_crc) == 37

    crc = crc16_ccitt(pre_crc)

    frame  = pre_crc
    frame += struct.pack('>H', crc)        # bytes 37–38
    frame += struct.pack('>BB', dst, src)  # bytes 39–40

    assert len(frame) == FRAME_SIZE
    return frame


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_frame(raw: bytes) -> BekoFrame:
    """
    Validate and unpack a raw 41-byte BEKO wire frame.

    Args:
        raw: bytes received from radio (must be exactly FRAME_SIZE bytes)

    Returns:
        BekoFrame: parsed frame (plaintext field is None — not decrypted yet)

    Raises:
        ValueError: on wrong length or CRC mismatch
    """
    if len(raw) < FRAME_SIZE:
        raise ValueError(
            f"Frame too short: {len(raw)} B (expected {FRAME_SIZE} B)"
        )

    raw = raw[:FRAME_SIZE]  # trim any trailing garbage

    frame_type, counter, flags = struct.unpack_from('>BHB', raw, 0)
    data     = raw[4:36]            # 32 bytes encrypted payload
    data_len = raw[36]              # should always be 32
    crc_rx   = struct.unpack_from('>H', raw, 37)[0]
    dst      = raw[39]
    src      = raw[40]

    # Verify CRC over bytes 0–36
    crc_calc = crc16_ccitt(raw[:37])
    if crc_calc != crc_rx:
        raise ValueError(
            f"CRC mismatch: rx=0x{crc_rx:04X} calc=0x{crc_calc:04X}"
        )

    return BekoFrame(
        type=frame_type,
        counter=counter,
        flags=flags,
        data=data,
        data_len=data_len,
        crc=crc_rx,
        dst=dst,
        src=src,
    )


# ---------------------------------------------------------------------------
# Payload builders / parsers
# ---------------------------------------------------------------------------

def build_cmd_payload(op_code: int, angle: int) -> bytes:
    """
    Build a 12-byte CMD payload (RPi → STM32).

    Args:
        op_code: CMD_OP_ABSOLUTE (1) or CMD_OP_RELATIVE (2)
        angle:   target angle 0–359° (big-endian uint16 on wire)

    Returns:
        12 bytes: op_code(1) + angle(2) + zeros(9)
    """
    if not 0 <= angle <= 359:
        raise ValueError(f"angle must be 0–359, got {angle}")
    if op_code not in (1, 2):
        raise ValueError(f"op_code must be 1 or 2, got {op_code}")
    return struct.pack('>BH', op_code, angle) + b'\x00' * 9


def build_ack_payload(result: int = 0) -> bytes:
    """
    Build a 12-byte ACK payload (RPi → STM32).

    Args:
        result: 0 = OK / confirmed, 1 = mismatch

    Returns:
        12 bytes: result(1) + zeros(11)
    """
    return struct.pack('>B', result) + b'\x00' * 11


def parse_telem_payload(plaintext: bytes) -> dict:
    """
    Parse a 12-byte TELEM plaintext (STM32 → RPi).

    Returns:
        dict with keys: servo_status (int), actual_angle (int)
    """
    servo_status, actual_angle = struct.unpack_from('>BH', plaintext)
    return {
        'servo_status': servo_status,
        'actual_angle': actual_angle,
    }


def parse_alarm_payload(plaintext: bytes) -> dict:
    """
    Parse a 12-byte ALARM plaintext (STM32 → RPi).

    Returns:
        dict with keys: alarm_code (int), angle_at_alarm (int)
    """
    alarm_code, angle_at_alarm = struct.unpack_from('>BH', plaintext)
    return {
        'alarm_code': alarm_code,
        'angle_at_alarm': angle_at_alarm,
    }
