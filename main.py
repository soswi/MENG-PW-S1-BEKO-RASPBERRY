from radio_handle import *
from crypto_layer import CryptoLayer
from time import sleep
import struct

RADIO_MODE = RadioMode.FSK
AES_KEY = bytes.fromhex("AE6852F8121067CC4BF7A5765577F39E")
crypto = CryptoLayer(AES_KEY)

FRAME_TYPE_CMD   = 0x01
FRAME_TYPE_TELEM = 0x02
FRAME_TYPE_ALARM = 0x03
FRAME_TYPE_ACK   = 0x04
TYPE_NAMES = {
    FRAME_TYPE_CMD: "CMD", FRAME_TYPE_TELEM: "TELEM",
    FRAME_TYPE_ALARM: "ALARM", FRAME_TYPE_ACK: "ACK",
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

    # Nowa struktura: type(1)+counter(2)+flags(1)+data(64)+data_len(1)+crc(2)+dst(1)+src(1) = 73B
    if len(raw) < 73:
        print(f"  WARN: za krótka ramka ({len(raw)}B < 73B)")
        return

    frame_type = raw[0]
    counter    = struct.unpack_from('<H', raw, 1)[0]
    flags      = raw[3]
    enc_data   = raw[4:68]    # 64 bajty pola data
    data_len   = raw[68]
    crc_recv   = struct.unpack_from('<H', raw, 69)[0]
    dst        = raw[71]
    src        = raw[72]

    # Weryfikacja CRC
    crc_calc = crc16(raw[:69])
    crc_ok = "OK" if crc_calc == crc_recv else f"FAIL (calc=0x{crc_calc:04X})"

    type_name = TYPE_NAMES.get(frame_type, f"0x{frame_type:02X}")
    print(f"  type={type_name}  seq={counter}  flags=0x{flags:02X}  crc={crc_ok}")
    print(f"  dst=0x{dst:02X}  src=0x{src:02X}  enc_len={data_len}B")

    if data_len < 21:  # min: 16(IV)+1(CT)+4(MIC)
        print(f"  WARN: enc_len za mały ({data_len}B)")
        return

    # Odszyfruj — enc_data to IV+CT+MIC jako latin-1 string
    enc_str = enc_data[:data_len].decode('latin-1')
    try:
        plaintext = crypto.decrypt(enc_str)
        print(f"  DECRYPT OK: '{plaintext.decode('ascii', errors='replace')}'")
    except ValueError as e:
        print(f"  DECRYPT FAIL: {e}")

def data_callback(data, rssi=None, index=None):
    print(f"\n--- Ramka #{index} (RSSI: {rssi} dBm) ---")
    parse_frame(data)

radio_handler = RadioHandler(RADIO_MODE, data_callback)
print("Czekam na zaszyfrowane ramki BEKO...")

try:
    while True:
        sleep(1)
except KeyboardInterrupt:
    print("Zatrzymano.")
finally:
    radio_handler.cleanup()