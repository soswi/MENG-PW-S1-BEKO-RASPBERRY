from radio_handle import *
from crypto_layer import CryptoLayer

AES_KEY = bytes.fromhex("AE6852F8121067CC4BF7A5765577F39E")

RADIO_MODE = RadioMode.FSK
SEND_DELAY = 5
SEND_MSG = b"Hello BEKO!"
SEND_MESSAGES = True

crypto = CryptoLayer(AES_KEY)


def data_callback(data, rssi=None, index=None):
    try:
        plaintext = crypto.decrypt(data)  # data to str — crypto sam konwertuje
        print(f"Odebrano: {plaintext}")
    except ValueError as e:
        print(f"[SECURITY] Ramka odrzucona: {e}")


radio_handler = RadioHandler(RADIO_MODE, data_callback)


if SEND_MESSAGES:
    def send_messages():
        while True:
            try:
                encrypted = crypto.encrypt(SEND_MSG)  # zwraca str — gotowe dla send()
                radio_handler.send(encrypted)
            except Exception as e:
                print(f"Błąd wysyłania: {e}")
            sleep(SEND_DELAY)

    send_thread = Thread(target=send_messages)
    send_thread.daemon = True
    send_thread.start()

try:
    while True:
        pass
except KeyboardInterrupt:
    print("Zatrzymano.")
finally:
    radio_handler.cleanup()