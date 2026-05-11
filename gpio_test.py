"""
gpio_test.py
============
Diagnoza RX na SX1276 w trybie FSK.
Ręcznie inicjalizuje radio w RX i czeka na pakiety przez 30s.
"""

import RPi.GPIO as GPIO
import spidev
import time

INTERRUPT_PIN = 22
INTERRUPT_PIN1 = 23
INTERRUPT_PIN2 = 24
RESET_PIN     = 25
SPI_PORT      = 0
SPI_CHANNEL   = 1

# SX1276 rejestry
REG_01_OP_MODE      = 0x01
REG_00_FIFO         = 0x00
REG_40_DIO_MAPPING1 = 0x40
REG_41_DIO_MAPPING2 = 0x41
REG_12_IRQ_FLAGS1   = 0x12
REG_13_IRQ_FLAGS2   = 0x13
REG_0D_RXCONFIG     = 0x0D
REG_32_PAYLOADLENGTH = 0x32

RF_OPMODE_MASK              = 0xF8
RFLR_OPMODE_LONGRANGEMODE_MASK = 0x7F
MODE_SLEEP       = 0x00
MODE_STDBY       = 0x01
MODE_RXCONTINUOUS = 0x05
LONG_RANGE_MODE_OFF = 0x00

RF_RXCONFIG_AFCAUTO_ON  = 0x10
RF_RXCONFIG_AGCAUTO_ON  = 0x08
RF_RXCONFIG_RXTRIGER_PREAMBLEDETECT = 0x06

RF_DIOMAPPING1_DIO0_MASK = 0x3F
RF_DIOMAPPING1_DIO1_MASK = 0xCF
RF_DIOMAPPING1_DIO2_MASK = 0xF3

interrupt_count = [0]
last_payload = [None]

def spi_read(spi, reg, length=1):
    if length == 1:
        return spi.xfer([reg & 0x7F, 0x00])[1]
    else:
        return spi.xfer([reg & 0x7F] + [0x00] * length)[1:]

def spi_write(spi, reg, val):
    if isinstance(val, int):
        spi.xfer([reg | 0x80, val])
    else:
        spi.xfer([reg | 0x80] + list(val))

def force_rx(spi):
    """Wymuś tryb FSK RX continuous."""
    # 1. Sleep
    temp = spi_read(spi, REG_01_OP_MODE) & RF_OPMODE_MASK
    spi_write(spi, REG_01_OP_MODE, temp | MODE_SLEEP)
    time.sleep(0.01)

    # 2. Wyłącz LoRa
    temp = spi_read(spi, REG_01_OP_MODE) & RFLR_OPMODE_LONGRANGEMODE_MASK
    spi_write(spi, REG_01_OP_MODE, temp | LONG_RANGE_MODE_OFF)

    # 3. DIO0 → PayloadReady (00), DIO2 → SyncAddress (0x0C)
    spi_write(spi, REG_40_DIO_MAPPING1,
              (spi_read(spi, REG_40_DIO_MAPPING1)
               & RF_DIOMAPPING1_DIO0_MASK
               & RF_DIOMAPPING1_DIO1_MASK
               & RF_DIOMAPPING1_DIO2_MASK) | 0x00 | 0x00 | 0x0C)
    spi_write(spi, REG_41_DIO_MAPPING2,
              (spi_read(spi, REG_41_DIO_MAPPING2) & 0x3F & 0xFE) | 0xC0 | 0x01)

    # 4. RxConfig: AFC auto + AGC auto + trigger on preamble detect
    spi_write(spi, REG_0D_RXCONFIG,
              RF_RXCONFIG_AFCAUTO_ON |
              RF_RXCONFIG_AGCAUTO_ON |
              RF_RXCONFIG_RXTRIGER_PREAMBLEDETECT)

    # 5. RX continuous
    temp = spi_read(spi, REG_01_OP_MODE) & RF_OPMODE_MASK
    spi_write(spi, REG_01_OP_MODE, temp | MODE_RXCONTINUOUS)
    time.sleep(0.01)

    mode = spi_read(spi, REG_01_OP_MODE)
    print(f"  OpMode po force_rx: 0x{mode:02X}  "
          f"(powinno być 0x{MODE_RXCONTINUOUS | (mode & 0xF8):02X})")

def on_interrupt(channel):
    interrupt_count[0] += 1
    # Odczytaj długość i payload z FIFO
    length = spi.xfer([REG_00_FIFO & 0x7F, 0x00])[1]
    if length > 0 and length <= 64:
        payload = spi.xfer([REG_00_FIFO & 0x7F] + [0x00] * length)[1:]
        last_payload[0] = bytes(payload)
        print(f"\n  INTERRUPT #{interrupt_count[0]}  "
              f"len={length}B  hex={bytes(payload).hex().upper()}")
    else:
        print(f"\n  INTERRUPT #{interrupt_count[0]}  len={length} (pusty lub błąd)")

    # Restart RX
    rxcfg = spi.xfer([REG_0D_RXCONFIG & 0x7F, 0x00])[1]
    spi.xfer([REG_0D_RXCONFIG | 0x80, rxcfg | 0x40])  # RestartRxWithoutPllLock

# ------------------------------------------------------------------ #

GPIO.setmode(GPIO.BCM)

# Reset chipu
GPIO.setup(RESET_PIN, GPIO.OUT)
GPIO.output(RESET_PIN, GPIO.LOW)
time.sleep(0.01)
GPIO.output(RESET_PIN, GPIO.HIGH)
time.sleep(0.1)

# SPI
spi = spidev.SpiDev()
spi.open(SPI_PORT, SPI_CHANNEL)
spi.max_speed_hz = 5000000

version = spi_read(spi, 0x42)
print(f"SX1276 version: 0x{version:02X}  (powinno być 0x12)")
if version != 0x12:
    print("BŁĄD: zły chip lub SPI nie działa!")
    GPIO.cleanup()
    spi.close()
    exit(1)

print(f"OpMode przed: 0x{spi_read(spi, REG_01_OP_MODE):02X}")

# GPIO
for pin in [INTERRUPT_PIN, INTERRUPT_PIN1, INTERRUPT_PIN2]:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    try:
        GPIO.remove_event_detect(pin)
    except Exception:
        pass

GPIO.add_event_detect(INTERRUPT_PIN, GPIO.RISING, callback=on_interrupt)

# Przestaw w RX
print("Przestawiam radio w FSK RX continuous...")
force_rx(spi)

print(f"\nNasłuchuję 30 sekund — zrestartuj STM32...\n")

deadline = time.time() + 30
tick = 0
while time.time() < deadline:
    remaining = int(deadline - time.time())
    irq1 = spi_read(spi, REG_12_IRQ_FLAGS1)
    irq2 = spi_read(spi, REG_13_IRQ_FLAGS2)
    mode = spi_read(spi, REG_01_OP_MODE)
    print(f"  [{remaining:2d}s] mode=0x{mode:02X}  "
          f"irq1=0x{irq1:02X}  irq2=0x{irq2:02X}  "
          f"PayloadReady={bool(irq2 & 0x04)}  "
          f"interrupts={interrupt_count[0]}     ",
          end='\r')
    time.sleep(1.0)

print(f"\n\nWynik: {interrupt_count[0]} przerwań w 30 sekund")
if interrupt_count[0] == 0:
    print("=> Brak przerwań. Sprawdź:")
    print("   1. Czy STM32 w ogóle nadaje (LED TX, oscyloskop)?")
    print("   2. Czy pin GPIO 22 jest podłączony do DIO0 modułu RFM95?")
    print("   3. Czy częstotliwość jest taka sama po obu stronach (868 MHz)?")
else:
    print("=> GPIO i RX działają. Problem był w tym że radio zostawało w TX.")

GPIO.cleanup()
spi.close()
