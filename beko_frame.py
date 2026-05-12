"""
beko_frame.py
=============
Serialization and deserialization of BEKO radio protocol frames.

Wire format (41 bytes):

  Offset  Size  Field      Byte order      Description
  ------  ----  ---------  --------------- ----------------------------------------
  0       1 B   type       —               Frame type (FRAME_TYPE_*)
  1       2 B   counter    little-endian   Sequence counter (C uint16_t on ARM)
  3       1 B   flags      —               Flag bits (FRAME_FLAG_* OR-ed together)
  4      32 B   data       —               Encrypted payload: IV(16)+CT(12)+MIC(4)
  36      1 B   data_len   —               Always 32
  37      2 B   crc        little-endian   CRC-16/CCITT over bytes 0–36 (C uint16_t)
  39      1 B   dst        —               Destination node address
  40      1 B   src        —               Source node address
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


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
        crc &= 0xFFFF
    return crc


@dataclass
class BekoFrame:
    type:     int
    counter:  int
    flags:    int
    data:     bytes
    data_len: int
    crc:      int
    dst:      int
    src:      int

    plaintext: Optional[bytes] = None

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


def build_frame(frame_type: int,
                counter: int,
                flags: int,
                encrypted_payload: bytes,
                dst: int,
                src: int) -> bytes:
    if len(encrypted_payload) != ENCRYPTED_SIZE:
        raise ValueError(
            f"encrypted_payload must be exactly {ENCRYPTED_SIZE} bytes, "
            f"got {len(encrypted_payload)}"
        )

    pre_crc  = struct.pack('<B',  frame_type)
    pre_crc += struct.pack('<H',  counter)
    pre_crc += struct.pack('<B',  flags)
    pre_crc += encrypted_payload
    pre_crc += struct.pack('<B',  ENCRYPTED_SIZE)

    assert len(pre_crc) == 37

    crc = crc16_ccitt(pre_crc)

    frame  = pre_crc
    frame += struct.pack('<H', crc)
    frame += struct.pack('BB', dst, src)

    assert len(frame) == FRAME_SIZE
    return frame


def parse_frame(raw: bytes) -> BekoFrame:
    if len(raw) < FRAME_SIZE:
        raise ValueError(
            f"Frame too short: {len(raw)} B (expected {FRAME_SIZE} B)"
        )

    raw = raw[:FRAME_SIZE]

    frame_type = raw[0]
    counter    = struct.unpack_from('<H', raw, 1)[0]
    flags      = raw[3]
    data       = raw[4:36]
    data_len   = raw[36]
    crc_rx     = struct.unpack_from('<H', raw, 37)[0]
    dst        = raw[39]
    src        = raw[40]

    crc_calc = crc16_ccitt(raw[:37])
    if crc_calc != crc_rx:
        raise ValueError(
            f"CRC mismatch: rx=0x{crc_rx:04X} calc=0x{crc_calc:04X}"
        )

    return BekoFrame(
        type=frame_type, counter=counter, flags=flags,
        data=data, data_len=data_len, crc=crc_rx,
        dst=dst, src=src,
    )


def build_cmd_payload(op_code: int, angle: int) -> bytes:
    if op_code not in (1, 2):
        raise ValueError(f"op_code must be 1 or 2, got {op_code}")
    if op_code == 1 and not (0 <= angle <= 359):
        raise ValueError(f"absolute angle must be 0–359, got {angle}")
    return struct.pack('>BH', op_code, angle & 0xFFFF) + b'\x00' * 9


def build_ack_payload(result: int = 0) -> bytes:
    return bytes([result]) + b'\x00' * 11


def parse_telem_payload(plaintext: bytes) -> dict:
    servo_status = plaintext[0]
    actual_angle = struct.unpack_from('>H', plaintext, 1)[0]
    return {
        'servo_status': servo_status,
        'actual_angle': actual_angle,
    }


def parse_alarm_payload(plaintext: bytes) -> dict:
    alarm_code     = plaintext[0]
    angle_at_alarm = struct.unpack_from('>H', plaintext, 1)[0]
    return {
        'alarm_code':    alarm_code,
        'angle_at_alarm': angle_at_alarm,
    }