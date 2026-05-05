from radio_handle import *
from crypto_layer import CryptoLayer
from time import sleep
from threading import Thread
import struct

# ============================================================
TX_RX_TOGGLE = True   # True = TX, False = RX
# ============================================================

AES_KEY      = bytes.fromhex("AE6852F8121067CC4BF7A5765577F39E")
SEND_MSG     = b"AZI=045"
SEND_DELAY   = 3
RADIO_MODE   = RadioMode.FSK

crypto = CryptoLayer(AES_KEY)

FRAME_TYPE_CMD   = 0x01
FRAME_TYPE_TELEM = 0x02
FRAME_TYPE_ALARM = 0x03
FRAME_TYPE_ACK   = 0x04
TYPE_NAMES = {
    FRAME_TYPE_CMD: "CMD", FRAME_TYPE_TELEM: "TELEM",
    FRAME_TYPE_ALARM: "ALARM", FRAME_TYPE_ACK: "ACK",
}
ADDR_CENTRAL = 0x01
ADDR_NODE1   = 0x02

seq = 0

def calc_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
    return crc & 0xFFFF

def build_frame(frame_type, counter, flags, enc_raw: bytes, dst, src) -> str:
    data_padded = enc_raw[:32].ljust(32, b'\x00')
    data_len    = len(enc_raw[:32])
    crc_offset  = 1 + 2 + 1 + 32 + 1   # = 37
    pre_crc = struct.pack('<BHB', frame_type, counter, flags) + \
              data_padded + struct.pack('<B', data_len)
    crc = calc_crc16(pre_crc)
    frame = pre_crc + struct.pack('<H', crc) + struct.pack('BB', dst, src)
    return frame.decode('latin-1')

def parse_frame(data: str):
    raw = bytes(ord(c) for c in data)
    print(f"  RAW ({len(raw)}B): {raw.hex().upper()}")

    if len(raw) < 41:
        print(f"  WARN: za krotka ramka ({len(raw)}B)")
        return

    frame_type = raw[0]
    counter    = struct.unpack_from('<H', raw, 1)[0]
    flags      = raw[3]
    enc_data   = raw[4:36]
    data_len   = raw[36]
    crc_recv   = struct.unpack_from('<H', raw, 37)[0]
    dst        = raw[39]
    src        = raw[40]

    crc_calc = calc_crc16(raw[:37])
    crc_ok = "OK" if crc_calc == crc_recv else f"FAIL (calc=0x{crc_calc:04X})"

    type_name = TYPE_NAMES.get(frame_type, f"0x{frame_type:02X}")
    print(f"  type={type_name}  seq={counter}  flags=0x{flags:02X}  crc={crc_ok}")
    print(f"  dst=0x{dst:02X}  src=0x{src:02X}  enc_len={data_len}B")

    if data_len < 21:
        print(f"  WARN: enc_len za maly ({data_len}B)")
        return

    enc_str = enc_data[:data_len].decode('latin-1')
    try:
        plaintext = crypto.decrypt(enc_str)
        print(f"  DECRYPT OK: '{plaintext.decode('ascii', errors='replace')}'")
    except ValueError as e:
        print(f"  DECRYPT FAIL: {e}")

def data_callback(data, rssi=None, index=None):
    print(f"\n--- Ramka #{index} (RSSI: {rssi} dBm) ---")
    parse_frame(data)

def tx_loop():
    global seq
    print("TX loop start\r\n")
    while True:
        try:
            enc_raw = bytes(ord(c) for c in crypto.encrypt(SEND_MSG))
            frame   = build_frame(FRAME_TYPE_CMD, seq, 0x00,
                                  enc_raw, ADDR_NODE1, ADDR_CENTRAL)
            radio_handler.send(frame)
            print(f"TX: seq={seq} enc_len={len(enc_raw)}B")
            seq += 1
        except Exception as e:
            print(f"Blad TX: {e}")
        sleep(SEND_DELAY)

def rx_loop():
    print("RX loop start")

radio_handler = RadioHandler(RADIO_MODE, data_callback)

if TX_RX_TOGGLE:
    t = Thread(target=tx_loop, daemon=True)
    t.start()
else:
    rx_loop()

try:
    while True:
        sleep(1)
except KeyboardInterrupt:
    print("Zatrzymano.")
finally:
    radio_handler.cleanup()