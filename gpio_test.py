"""
gpio_test.py
============
Uruchamia pełny FSK stack (tak jak main.py), wysyła CMD angle=50
i monitoruje rejestry SX1276 co 50ms podczas oczekiwania na TELEM.
Pokazuje dokładnie w którym momencie radio jest w RX i czy PayloadReady
kiedykolwiek się pojawia.

Uruchom zamiast main.py, zrestartuj STM32 przed uruchomieniem.
"""

import sys, time, threading, spidev
sys.path.insert(0, '.')

import RPi.GPIO as GPIO
from radio_handle import RadioHandler, RadioMode
from radio_controller import RadioController
from beko_protocol import CMD_OP_ABSOLUTE, AES_KEY
from beko_frame import build_frame, build_cmd_payload, crc16_ccitt
from beko_protocol import (FRAME_TYPE_CMD, FRAME_FLAG_ACK_REQ,
                            ADDR_NODE1, ADDR_CENTRAL)
from crypto_layer import CryptoLayer

REG_01_OP_MODE    = 0x01
REG_12_IRQ_FLAGS1 = 0x12
REG_13_IRQ_FLAGS2 = 0x13
REG_40_DIO_MAP1   = 0x40

MODE_NAMES = {
    0x00: "SLEEP",   0x01: "STDBY",  0x02: "FSTX",
    0x03: "TX",      0x04: "FSRX",   0x05: "RX",
    0x09: "FSK+STDBY", 0x0B: "FSK+TX_PREP",
    0x0B: "FSK+TX",  0x0C: "FSK+TX", 0x0D: "FSK+RX",
}

stop_monitor = threading.Event()

def monitor_registers(spi):
    """Czyta rejestry co 50ms i drukuje zmiany."""
    last = {}
    while not stop_monitor.is_set():
        try:
            mode = spi.xfer([REG_01_OP_MODE, 0x00])[1]
            irq1 = spi.xfer([REG_12_IRQ_FLAGS1, 0x00])[1]
            irq2 = spi.xfer([REG_13_IRQ_FLAGS2, 0x00])[1]
            dio  = spi.xfer([REG_40_DIO_MAP1, 0x00])[1]

            state = (mode, irq1, irq2)
            if state != last.get('state'):
                payload_ready = bool(irq2 & 0x04)
                packet_sent   = bool(irq2 & 0x08)
                rx_ready      = bool(irq1 & 0x40)
                mode_name     = MODE_NAMES.get(mode, f"0x{mode:02X}")
                print(f"\n  [REG] mode={mode_name}({mode:#04x})  "
                      f"irq1={irq1:#04x}  irq2={irq2:#04x}  "
                      f"DIO_MAP={dio:#04x}  "
                      f"PayloadReady={payload_ready}  "
                      f"PacketSent={packet_sent}  "
                      f"RxReady={rx_ready}")
                last['state'] = state
        except Exception as e:
            print(f"\n  [REG] SPI error: {e}")
        time.sleep(0.05)

# ------------------------------------------------------------------ #

print("Inicjalizuję radio stack...")
controller = RadioController(mode=RadioMode.FSK)
controller.start()

# Otwórz drugi SPI handle tylko do odczytu rejestrów (monitor)
spi_mon = spidev.SpiDev()
spi_mon.open(0, 1)
spi_mon.max_speed_hz = 1000000

print("\nStart monitora rejestrów (co 50ms)...")
t = threading.Thread(target=monitor_registers, args=(spi_mon,), daemon=True)
t.start()

print("\nWysyłam CMD angle=50 za 2 sekundy... (zrestartuj STM32 teraz!)\n")
time.sleep(2)

print("→ send_cmd(50)...")
result = controller.send_cmd(50, CMD_OP_ABSOLUTE)

stop_monitor.set()
time.sleep(0.1)

print(f"\nWynik: {result}")
controller.stop()
spi_mon.close()
