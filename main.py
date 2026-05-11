"""
main.py
=======
Terminal menu for testing BEKO radio communication with STM32.
Includes RX diagnostic to verify FSK interrupt and callback chain.
"""

import logging
import sys
import threading
from time import sleep, time
from radio_handle import RadioHandler, RadioMode
from radio_controller import RadioController
from beko_protocol import CMD_OP_ABSOLUTE, CMD_OP_RELATIVE

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("beko.main")

MENU = """
╔══════════════════════════════════════╗
║    BEKO — terminal testowy           ║
╠══════════════════════════════════════╣
║  1  Wyślij azymut (absolutny)        ║
║  2  Wyślij azymut (względny)         ║
║  3  Odblokuj węzeł (UNLOCK)          ║
║  4  Zablokuj węzeł (LOCK)            ║
║  5  Pokaż status                     ║
║  6  Test RX — nasłuchuj 10s          ║
║  7  Test TX — wyślij surowe 41B      ║
║  0  Wyjście                          ║
╚══════════════════════════════════════╝
"""


def print_result(result: dict):
    if result.get("ok"):
        print("  ✓ OK")
    else:
        print(f"  ✗ BŁĄD: {result.get('error')}")
    if result.get("actual_angle") is not None:
        print(f"  Kąt rzeczywisty : {result['actual_angle']}°")
    if result.get("servo_status") is not None:
        print(f"  Servo status    : {result['servo_status']}")
    if result.get("retries"):
        print(f"  Próby           : {result['retries']}")


def run_rx_test(controller: RadioController):
    """
    Opcja 6 — nasłuchuj przez 10 sekund i loguj WSZYSTKO co przychodzi.
    Tymczasowo podmienia on_recv FSK drivera żeby zobaczyć surowe bajty
    zanim przejdą przez RadioHandler i RadioController.
    """
    print("\n  [RX TEST] Nasłuchuję 10 sekund — wyślij cokolwiek z STM32...")
    print("  (Ctrl+C żeby przerwać wcześniej)\n")

    raw_count = [0]

    # Podpinamy się bezpośrednio pod FSK driver — omijamy RadioHandler
    original_on_recv = controller._handler.fsk_handler.on_recv

    def raw_sniffer(data, rssi=None, index=None):
        raw_count[0] += 1
        raw_bytes = bytes(data) if data else b''
        print(f"  [RAW #{raw_count[0]}] {len(raw_bytes)}B  RSSI={rssi}  "
              f"hex={raw_bytes.hex().upper()}")
        # Przekaż dalej do normalnego handlera
        original_on_recv(data, rssi, index)

    controller._handler.fsk_handler.on_recv = raw_sniffer

    # Upewnij się że radio jest w RX
    controller._handler.start_rx()
    print(f"  Radio mode po start_rx: "
          f"{controller._handler.fsk_handler._mode}")

    try:
        deadline = time() + 10
        while time() < deadline:
            remaining = int(deadline - time())
            print(f"  Czekam... {remaining}s  "
                  f"(odebranych pakietów: {raw_count[0]})",
                  end='\r')
            sleep(0.5)
    except KeyboardInterrupt:
        pass

    print(f"\n  [RX TEST] Zakończono. Odebrano {raw_count[0]} pakietów.")

    # Przywróć oryginalny handler
    controller._handler.fsk_handler.on_recv = original_on_recv


def run_tx_raw_test(controller: RadioController):
    """
    Opcja 7 — buduje surową ramkę CMD i wysyła, drukując hex przed wysłaniem.
    Pozwala porównać wysyłane bajty z tym co STM32 raportuje w logach.
    """
    import struct
    from beko_frame import build_frame, build_cmd_payload, crc16_ccitt
    from beko_protocol import (FRAME_TYPE_CMD, FRAME_FLAG_ACK_REQ,
                                ADDR_NODE1, ADDR_CENTRAL, CMD_OP_ABSOLUTE,
                                ENCRYPTED_SIZE)
    from crypto_layer import CryptoLayer
    from beko_protocol import AES_KEY

    angle = 50
    crypto = CryptoLayer(AES_KEY)
    payload = build_cmd_payload(CMD_OP_ABSOLUTE, angle)
    enc = crypto.encrypt(payload)
    raw = build_frame(FRAME_TYPE_CMD, 0, FRAME_FLAG_ACK_REQ,
                      enc, ADDR_NODE1, ADDR_CENTRAL)

    print(f"\n  [TX RAW] Ramka {len(raw)}B:")
    print(f"  hex = {raw.hex().upper()}")
    print(f"  type=0x{raw[0]:02X}  "
          f"counter=0x{struct.unpack_from('<H', raw, 1)[0]:04X}  "
          f"flags=0x{raw[3]:02X}")
    crc_in_frame = struct.unpack_from('<H', raw, 37)[0]
    crc_calc = crc16_ccitt(raw[:37])
    print(f"  CRC w ramce=0x{crc_in_frame:04X}  "
          f"wyliczony=0x{crc_calc:04X}  "
          f"{'OK' if crc_in_frame == crc_calc else 'BŁĄD!'}")
    print(f"  dst=0x{raw[39]:02X}  src=0x{raw[40]:02X}")

    confirm = input("  Wysłać? [t/N]: ").strip().lower()
    if confirm == 't':
        controller._handler.send(raw.decode('latin-1'))
        print("  Wysłano.")
    else:
        print("  Anulowano.")


def run_menu(controller: RadioController):
    while True:
        print(MENU)
        choice = input("Wybierz opcję: ").strip()

        if choice == "1":
            try:
                angle = int(input("  Azymut [0–359]: ").strip())
            except ValueError:
                print("  Nieprawidłowa wartość.")
                continue
            print(f"  → CMD absolutny {angle}°…")
            result = controller.send_cmd(angle, CMD_OP_ABSOLUTE)
            print_result(result)

        elif choice == "2":
            try:
                delta = int(input("  Delta [np. +10 lub -10]: ").strip())
            except ValueError:
                print("  Nieprawidłowa wartość.")
                continue
            angle_enc = delta & 0xFFFF
            print(f"  → CMD względny delta={delta} (enc=0x{angle_enc:04X})…")
            result = controller.send_cmd(angle_enc, CMD_OP_RELATIVE)
            print_result(result)

        elif choice == "3":
            print("  → UNLOCK…")
            result = controller.send_unlock()
            print_result(result)

        elif choice == "4":
            print("  → LOCK…")
            result = controller.send_lock()
            print_result(result)

        elif choice == "5":
            status = controller.get_status()
            print()
            for k, v in status.items():
                print(f"  {k:<16}: {v}")

        elif choice == "6":
            run_rx_test(controller)

        elif choice == "7":
            run_tx_raw_test(controller)

        elif choice == "0":
            print("  Zatrzymuję…")
            break

        else:
            print("  Nieznana opcja.")


if __name__ == "__main__":
    controller = RadioController(mode=RadioMode.FSK)
    controller.start()
    try:
        run_menu(controller)
    except KeyboardInterrupt:
        print("\n  Przerwano (Ctrl+C).")
    finally:
        controller.stop()
