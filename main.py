from radio_handle import *
from time import sleep
import struct

RADIO_MODE = RadioMode.FSK
SEND_MESSAGES = False

FRAME_TYPE_CMD   = 0x01
FRAME_TYPE_TELEM = 0x02
FRAME_TYPE_ALARM = 0x03
FRAME_TYPE_ACK   = 0x04

ADDR_CENTRAL = 0x01
ADDR_NODE1   = 0x02

TYPE_NAMES = {
    FRAME_TYPE_CMD:   "CMD",
    FRAME_TYPE_TELEM: "TELEM",
    FRAME_TYPE_ALARM: "ALARM",
    FRAME_TYPE_ACK:   "ACK",
}

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
    return crc & 0xFFFF

def parse_frame(data: str):
    raw = bytes(ord(c) for c in data)
    print(f"  RAW ({len(raw)}B): {raw.hex().upper()}")

    # beko_frame_t: type(1) counter(2) flags(1) data(8) data_len(1) crc(2) dst(1) src(1) = 17B
    if len(raw) < 17:
        print(f"  WARN: za krótka ramka ({len(raw)}B < 17B)")
        return

    frame_type = raw[0]
    counter    = struct.unpack_from('<H', raw, 1)[0]
    flags      = raw[3]
    payload    = raw[4:12]
    data_len   = raw[12]
    crc_recv   = struct.unpack_from('<H', raw, 13)[0]
    dst        = raw[15]
    src        = raw[16]

    # Weryfikacja CRC
    crc_calc = crc16(raw[:13])  # wszystko przed polem crc
    crc_ok = "OK" if crc_calc == crc_recv else f"FAIL (calc=0x{crc_calc:04X})"

    type_name = TYPE_NAMES.get(frame_type, f"0x{frame_type:02X}")
    payload_str = payload[:data_len].decode('ascii', errors='replace')

    print(f"  type={type_name}  seq={counter}  flags=0x{flags:02X}")
    print(f"  payload[{data_len}B]: {payload[:data_len].hex().upper()} = '{payload_str}'")
    print(f"  crc=0x{crc_recv:04X}  {crc_ok}  dst=0x{dst:02X}  src=0x{src:02X}")


def data_callback(data, rssi=None, index=None):
    print(f"\n--- Odebrano ramkę #{index} (RSSI: {rssi} dBm) ---")
    parse_frame(data)


radio_handler = RadioHandler(RADIO_MODE, data_callback)
print("Czekam na ramki BEKO...")

try:
    while True:
        sleep(1)
except KeyboardInterrupt:
    print("Zatrzymano.")
finally:
    radio_handler.cleanup()