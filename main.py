"""
main.py
=======
Terminal menu for testing BEKO radio communication with STM32.
Temporary replacement for the Flask API — use this to verify the
radio layer works before wiring up the web interface.

Usage:
    python3 main.py
"""

import logging
import sys
from radio_handle import RadioMode
from radio_controller import RadioController
from beko_protocol import CMD_OP_ABSOLUTE, CMD_OP_RELATIVE

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

MENU = """
╔══════════════════════════════════╗
║    BEKO — terminal testowy       ║
╠══════════════════════════════════╣
║  1  Wyślij azymut (absolutny)    ║
║  2  Wyślij azymut (względny)     ║
║  3  Odblokuj węzeł (UNLOCK)      ║
║  4  Zablokuj węzeł (LOCK)        ║
║  5  Pokaż status                 ║
║  0  Wyjście                      ║
╚══════════════════════════════════╝
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
            # Względny kąt kodowany jako uint16 (ujemne jako dopełnienie do 2)
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

        elif choice == "0":
            print("  Zatrzymuję…")
            break

        else:
            print("  Nieznana opcja.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    controller = RadioController(mode=RadioMode.FSK)
    controller.start()
    try:
        run_menu(controller)
    except KeyboardInterrupt:
        print("\n  Przerwano (Ctrl+C).")
    finally:
        controller.stop()
