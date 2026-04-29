from radio_handle import *
from time import sleep

RADIO_MODE = RadioMode.FSK
SEND_MESSAGES = False


def data_callback(data, rssi=None, index=None):
    raw = bytes(ord(c) for c in data)
    print(f"[RX] RSSI={rssi} dBm | len={len(raw)} | hex={raw.hex().upper()} | ascii={repr(raw)}")


radio_handler = RadioHandler(RADIO_MODE, data_callback)

print(f"Radio mode: {radio_handler.mode}")
print(f"FSK handler: {radio_handler.fsk_handler}")

print("Czekam na dane...")

try:
    while True:
        sleep(1)
except KeyboardInterrupt:
    print("Zatrzymano.")
finally:
    radio_handler.cleanup()