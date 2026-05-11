"""
gpio_test.py
============
Sprawdza czy GPIO interrupt na DIO0 (pin 22) w ogóle działa.
Uruchom osobno przed main.py.

Wyniki:
  - Jeśli widzisz "INTERRUPT pin 22" gdy STM32 nadaje → GPIO działa,
    problem jest w SPI odczycie FIFO.
  - Jeśli cisza → GPIO edge detection nie działa lub DIO0 nie jest
    mapowany na PayloadReady w trybie FSK RX.
"""

import RPi.GPIO as GPIO
import spidev
import time

INTERRUPT_PIN = 22
RESET_PIN     = 25
SPI_PORT      = 0
SPI_CHANNEL   = 1

# SX1276 rejestry
REG_01_OP_MODE       = 0x01
REG_40_DIO_MAPPING1  = 0x40
REG_41_DIO_MAPPING2  = 0x41
REG_12_IRQ_FLAGS     = 0x12   # FSK: RegIrqFlags1
REG_13_IRQ_FLAGS2    = 0x13   # FSK: RegIrqFlags2

interrupt_count = [0]

def spi_read(spi, reg):
    return spi.xfer([reg & 0x7F, 0x00])[1]

def spi_write(spi, reg, val):
    spi.xfer([reg | 0x80, val])

def on_interrupt(channel):
    interrupt_count[0] += 1
    print(f"  INTERRUPT pin {channel}  #{interrupt_count[0]}")

GPIO.setmode(GPIO.BCM)

# Reset
GPIO.setup(RESET_PIN, GPIO.OUT)
GPIO.output(RESET_PIN, GPIO.LOW)
time.sleep(0.01)
GPIO.output(RESET_PIN, GPIO.HIGH)
time.sleep(0.01)

# SPI
spi = spidev.SpiDev()
spi.open(SPI_PORT, SPI_CHANNEL)
spi.max_speed_hz = 1000000

# Odczytaj wersję chipu
version = spi_read(spi, 0x42)
print(f"SX1276 version: 0x{version:02X}  (powinno być 0x12)")

# Odczytaj aktualny tryb
opmode = spi_read(spi, REG_01_OP_MODE)
print(f"OpMode: 0x{opmode:02X}")

# Odczytaj DIO mapping
dio1 = spi_read(spi, REG_40_DIO_MAPPING1)
dio2 = spi_read(spi, REG_41_DIO_MAPPING2)
print(f"DIO_MAPPING1: 0x{dio1:02X}  DIO_MAPPING2: 0x{dio2:02X}")

# IRQ flags
irq1 = spi_read(spi, REG_12_IRQ_FLAGS)
irq2 = spi_read(spi, REG_13_IRQ_FLAGS2)
print(f"IrqFlags1: 0x{irq1:02X}  IrqFlags2: 0x{irq2:02X}")
print(f"  ModeReady:    {bool(irq1 & 0x80)}")
print(f"  RxReady:      {bool(irq1 & 0x40)}")
print(f"  PayloadReady: {bool(irq2 & 0x04)}")
print(f"  PacketSent:   {bool(irq2 & 0x08)}")

# GPIO interrupt
GPIO.setup(INTERRUPT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
try:
    GPIO.remove_event_detect(INTERRUPT_PIN)
except Exception:
    pass
GPIO.add_event_detect(INTERRUPT_PIN, GPIO.RISING, callback=on_interrupt)

print(f"\nNasłuchuję na GPIO {INTERRUPT_PIN} przez 15 sekund...")
print("Teraz zrestartuj STM32 i wyślij coś ręcznie lub poczekaj na alarm.\n")

deadline = time.time() + 15
while time.time() < deadline:
    remaining = int(deadline - time.time())

    # Sprawdź IRQ flags co sekundę
    irq1 = spi_read(spi, REG_12_IRQ_FLAGS)
    irq2 = spi_read(spi, REG_13_IRQ_FLAGS2)
    opmode = spi_read(spi, REG_01_OP_MODE)

    print(f"  [{remaining:2d}s] OpMode=0x{opmode:02X}  "
          f"IrqFlags1=0x{irq1:02X}  IrqFlags2=0x{irq2:02X}  "
          f"PayloadReady={bool(irq2 & 0x04)}  "
          f"interrupts={interrupt_count[0]}",
          end='\r')
    time.sleep(1.0)

print(f"\n\nWynik: {interrupt_count[0]} przerwań w 15 sekund")
if interrupt_count[0] == 0:
    print("PROBLEM: GPIO edge detection nie działa lub DIO0 nie jest")
    print("         mapowany na PayloadReady w trybie FSK RX.")
    print("         Sprawdź czy pin 22 jest podłączony do DIO0 modułu.")
else:
    print("GPIO działa — problem jest w SPI odczycie FIFO lub wyżej.")

GPIO.cleanup()
spi.close()
