"""
beko_protocol.py
================
Single source of truth for all BEKO radio protocol constants.
Both frame.py and radio_controller.py import from here.

Do NOT define these values anywhere else in the codebase.
"""

# ---------------------------------------------------------------------------
# Node addresses
# ---------------------------------------------------------------------------
ADDR_CENTRAL = 0x01   # Raspberry Pi — always src when RPi sends
ADDR_NODE1   = 0x02   # STM32 node 1

# ---------------------------------------------------------------------------
# Frame types
# ---------------------------------------------------------------------------
FRAME_TYPE_CMD   = 0x01   # Command: azimuth set-point   RPi → STM32
FRAME_TYPE_TELEM = 0x02   # Telemetry: angle + status    STM32 → RPi
FRAME_TYPE_ALARM = 0x03   # Alarm: anomaly report        STM32 → RPi
FRAME_TYPE_ACK   = 0x04   # Acknowledgement              RPi → STM32

FRAME_TYPE_NAMES = {
    FRAME_TYPE_CMD:   "CMD",
    FRAME_TYPE_TELEM: "TELEM",
    FRAME_TYPE_ALARM: "ALARM",
    FRAME_TYPE_ACK:   "ACK",
}

# ---------------------------------------------------------------------------
# Flag bits (OR-ed together in the flags byte)
# ---------------------------------------------------------------------------
FRAME_FLAG_ACK_REQ  = (1 << 0)  # 0x01  Sender requests an ACK reply
FRAME_FLAG_ACK      = (1 << 1)  # 0x02  This frame IS an ACK
FRAME_FLAG_ALARM    = (1 << 2)  # 0x04  Frame carries an alarm code
FRAME_FLAG_FAILSAFE = (1 << 3)  # 0x08  Node is in fail-safe / locked mode
FRAME_FLAG_NAK      = (1 << 4)  # 0x10  Command rejected (rotation failed)
FRAME_FLAG_UNLOCK   = (1 << 5)  # 0x20  RPi orders node to unlock / reset
FRAME_FLAG_LOCK     = (1 << 6)  # 0x40  RPi orders node to lock immediately

# ---------------------------------------------------------------------------
# CMD payload op-codes
# ---------------------------------------------------------------------------
CMD_OP_ABSOLUTE = 1   # Rotate TO this angle (0–359°)
CMD_OP_RELATIVE = 2   # Rotate BY this delta

# ---------------------------------------------------------------------------
# TELEM payload servo status codes
# ---------------------------------------------------------------------------
SERVO_STATUS_OK          = 0
SERVO_STATUS_ANGLE_ERROR = 1
SERVO_STATUS_ROTOR_STOP  = 2

# ---------------------------------------------------------------------------
# ALARM payload alarm codes
# ---------------------------------------------------------------------------
ALARM_CODE_ROTOR_STOP  = 0x00  # Rotor blocked — no movement detected
ALARM_CODE_ANGLE_ERROR = 0x01  # Angle tolerance exceeded
ALARM_CODE_LINK_LOST   = 0x02  # No valid frame received for 30 s

ALARM_CODE_NAMES = {
    ALARM_CODE_ROTOR_STOP:  "ROTOR_STOP",
    ALARM_CODE_ANGLE_ERROR: "ANGLE_ERROR",
    ALARM_CODE_LINK_LOST:   "LINK_LOST",
}

# ---------------------------------------------------------------------------
# Frame geometry
# ---------------------------------------------------------------------------
FRAME_SIZE       = 41   # Total fixed wire size in bytes
BEKO_PAYLOAD_SIZE = 12  # Plaintext payload always 12 bytes (zero-padded)
ENCRYPTED_SIZE   = 32   # IV(16) + CT(12) + MIC(4)

# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------
AES_KEY = bytes([
    0xAE, 0x68, 0x52, 0xF8, 0x12, 0x10, 0x67, 0xCC,
    0x4B, 0xF7, 0xA5, 0x76, 0x55, 0x77, 0xF3, 0x9E
])

# ---------------------------------------------------------------------------
# Protocol timing (milliseconds)
# ---------------------------------------------------------------------------
TELEM_TIMEOUT_MS  = 10000  # Max wait for TELEM after sending CMD
ACK_TIMEOUT_MS    = 2000   # Max wait for ACK from STM32
ALARM_RETRY_MS    = 5000   # STM32 retransmits ALARM every 5 s
FAILSAFE_TIMEOUT_S = 30    # STM32 enters fail-safe after 30 s silence
CMD_MAX_RETRIES   = 1      # Total TX attempts per send_cmd() call (1 = no retries)
