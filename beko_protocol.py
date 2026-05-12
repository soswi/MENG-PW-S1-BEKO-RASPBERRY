"""
beko_protocol.py
================
Single source of truth for all BEKO radio protocol constants.
Both frame.py and radio_controller.py import from here.

Do NOT define these values anywhere else in the codebase.
"""

ADDR_CENTRAL = 0x01
ADDR_NODE1   = 0x02

FRAME_TYPE_CMD   = 0x01
FRAME_TYPE_TELEM = 0x02
FRAME_TYPE_ALARM = 0x03
FRAME_TYPE_ACK   = 0x04

FRAME_TYPE_NAMES = {
    FRAME_TYPE_CMD:   "CMD",
    FRAME_TYPE_TELEM: "TELEM",
    FRAME_TYPE_ALARM: "ALARM",
    FRAME_TYPE_ACK:   "ACK",
}

FRAME_FLAG_ACK_REQ  = (1 << 0)
FRAME_FLAG_ACK      = (1 << 1)
FRAME_FLAG_ALARM    = (1 << 2)
FRAME_FLAG_FAILSAFE = (1 << 3)
FRAME_FLAG_NAK      = (1 << 4)
FRAME_FLAG_UNLOCK   = (1 << 5)
FRAME_FLAG_LOCK     = (1 << 6)

CMD_OP_ABSOLUTE = 1
CMD_OP_RELATIVE = 2

SERVO_STATUS_OK          = 0
SERVO_STATUS_ANGLE_ERROR = 1
SERVO_STATUS_ROTOR_STOP  = 2

ALARM_CODE_ROTOR_STOP  = 0x00
ALARM_CODE_ANGLE_ERROR = 0x01
ALARM_CODE_LINK_LOST   = 0x02

ALARM_CODE_NAMES = {
    ALARM_CODE_ROTOR_STOP:  "ROTOR_STOP",
    ALARM_CODE_ANGLE_ERROR: "ANGLE_ERROR",
    ALARM_CODE_LINK_LOST:   "LINK_LOST",
}

FRAME_SIZE        = 41
BEKO_PAYLOAD_SIZE = 12
ENCRYPTED_SIZE    = 32

AES_KEY = bytes([
    0xAE, 0x68, 0x52, 0xF8, 0x12, 0x10, 0x67, 0xCC,
    0x4B, 0xF7, 0xA5, 0x76, 0x55, 0x77, 0xF3, 0x9E
])

TELEM_TIMEOUT_MS   = 5000
ACK_TIMEOUT_MS     = 2000
ALARM_RETRY_MS     = 5000
FAILSAFE_TIMEOUT_S = 30
CMD_MAX_RETRIES    = 1      # Total TX attempts per send_cmd() call (1 = no retries)