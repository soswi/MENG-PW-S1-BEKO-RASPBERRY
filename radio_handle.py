"""
MIT License

Copyright (c) 2024 BEER-TEAM (Piotr Polnau, Jan Sosulski, Piotr Baprawski)
"""

from pyLoraRFM9x import LoRa, ModemConfig
from sx_1276_driver.radio_driver import FSK
import RPi.GPIO as GPIO
import radio_defines
from enum import Enum, auto
from time import sleep

_FSK_FRAME_AIR_TIME_S = 0.130

_REG_01_OP_MODE    = 0x01
_REG_00_FIFO       = 0x00
_REG_13_IRQ2       = 0x13
_REG_40_DIOMAP1    = 0x40
_REG_41_DIOMAP2    = 0x41
_MODE_SLEEP        = 0x00
_MODE_STDBY        = 0x01
_MODE_RXCONT       = 0x05


class RadioMode(Enum):
    FSK  = auto()
    LORA = auto()

    def __str__(self) -> str:
        return self.name


class RadioHandler:
    def __init__(self, mode, data_callback):
        GPIO.setmode(GPIO.BCM)
        self.mode          = mode
        self.data_callback = data_callback

        if self.mode == RadioMode.FSK:
            self.fsk_handler = FSK(
                spiport=radio_defines.SPI_PORT,
                channel=radio_defines.SPI_CHANNEL,
                interrupt=radio_defines.INTERRUPT_PIN,
                interrupt1=radio_defines.INTERRUPT_PIN1,
                interrupt2=radio_defines.INTERRUPT_PIN2,
                reset_pin=radio_defines.RESET_PIN,
                freq=radio_defines.FSK_FREQ,
                tx_power=radio_defines.FSK_TX_POWER,
                fixLEN=radio_defines.FSK_FIX_LEN,
                payload_len=radio_defines.FSK_PAYLOAD_LEN,
            )
            self._spi = self.fsk_handler.spi
            self.fsk_handler.on_recv = self.handle_received_data
            self._force_rx()

        elif self.mode == RadioMode.LORA:
            self.lora_handler = LoRa(
                spi_channel=radio_defines.SPI_CHANNEL,
                interrupt_pin=radio_defines.INTERRUPT_PIN,
                my_address=radio_defines.LORA_ADDR,
                spi_port=radio_defines.SPI_PORT,
                reset_pin=radio_defines.RESET_PIN,
                freq=radio_defines.LORA_FREQ,
                tx_power=radio_defines.LORA_POWER,
                modem_config=radio_defines.LORA_MODEM_CONFIG,
                acks=radio_defines.LORA_ACKS,
                receive_all=True,
            )
            self.lora_handler.on_recv = self.handle_received_data
            self.lora_handler.set_mode_rx()

        else:
            raise ValueError("Invalid mode. Choose 'fsk' or 'lora'.")

        print(f"{self.mode} handler is running... Waiting for data.")

    # ------------------------------------------------------------------ #

    def _spi_r(self, reg):
        return self._spi.xfer([reg & 0x7F, 0x00])[1]

    def _spi_w(self, reg, val):
        self._spi.xfer([reg | 0x80, val])

    def _force_rx(self):
        """
        Full hardware reset + reconfigure to reliably enter FSK RxContinuous.

        Three-part fix for the TX→RX transition failure:
          1. GPIO.remove_event_detect — flushes any queued TxDone callback
             that would otherwise fire (with stale _mode=RXCONT) and call
             RF_RXCONFIG_RESTARTRXWITHOUTPLLLOCK while the chip is in FSRx,
             aborting the PLL lock and dropping the chip back to SLEEP.
          2. Force _mode=SLEEP before SX1276SetRx_fsk() — set_mode_rx_fsk()
             has a guard "if _mode != RXCONT" that silently skips the mode
             write when _mode is stale; forcing SLEEP ensures it always runs.
          3. GPIO.add_event_detect after confirmed RXCONT — clean edge
             detector with no history of stale TX edges.
        """
        # Hardware reset — separate GPIO line, no SPI lock needed
        GPIO.setup(radio_defines.RESET_PIN, GPIO.OUT)
        GPIO.output(radio_defines.RESET_PIN, GPIO.LOW)
        sleep(0.010)
        GPIO.output(radio_defines.RESET_PIN, GPIO.HIGH)
        sleep(0.010)

        # Flush any queued TxDone callback BEFORE acquiring the lock.
        # Calling remove_event_detect while holding _hw_lock would deadlock
        # if the callback thread is blocked inside _spi_read() on that lock.
        GPIO.remove_event_detect(radio_defines.INTERRUPT_PIN)

        # _hw_lock is an RLock — nested acquisitions inside driver methods are safe.
        with self.fsk_handler._hw_lock:
            # Restore all registers wiped by the reset
            self.fsk_handler.SX1276Init()
            self.fsk_handler.SX1276SetChannel()
            self.fsk_handler.SX1276SetTxConfig(fixLEN=radio_defines.FSK_FIX_LEN)
            self.fsk_handler.SX1276SetRxConfig(
                fixLEN=radio_defines.FSK_FIX_LEN,
                payload_len=radio_defines.FSK_PAYLOAD_LEN,
            )
            # Force _mode to SLEEP so set_mode_rx_fsk()'s guard never skips it
            self.fsk_handler._mode = _MODE_SLEEP
            # Set DIO0→PayloadReady, clear RX buffers, write RXCONT to RegOpMode
            self.fsk_handler.SX1276SetRx_fsk()
            # Poll until hardware confirms RXCONT (250 ms max)
            for _ in range(125):
                sleep(0.002)
                if (self._spi_r(_REG_01_OP_MODE) & 0x07) == _MODE_RXCONT:
                    break

        # Re-arm DIO0 edge detection with a clean slate — no stale TX edges
        GPIO.add_event_detect(radio_defines.INTERRUPT_PIN, GPIO.RISING,
                              callback=self.fsk_handler._handle_interrupt)

        mode = self._spi_r(_REG_01_OP_MODE)
        irq2 = self._spi_r(_REG_13_IRQ2)
        print(f"  [RX] mode=0x{mode:02X}  PayloadReady={bool(irq2 & 0x04)}")

    # ------------------------------------------------------------------ #

    def start_rx(self):
        if self.mode == RadioMode.FSK:
            self._force_rx()
        elif self.mode == RadioMode.LORA:
            self.lora_handler.set_mode_rx()

    def handle_received_data(self, data, rssi=None, index=None):
        if self.mode == RadioMode.FSK:
            if data:
                decoded = ''.join(chr(b) for b in data)
                print(f"Received FSK data: (RSSI: {rssi} dBm, Index: {index})")
                self.data_callback(decoded, rssi, index)
            else:
                print("Received empty or noise data.")
        elif self.mode == RadioMode.LORA:
            int_data = [int(b) for b in data.message]
            int_data.insert(0, data.header_flags)
            int_data.insert(0, data.header_id)
            int_data.insert(0, data.header_from)
            int_data.insert(0, data.header_to)
            decoded = ''.join(chr(b) for b in int_data)
            print(f"Received LoRa data: (RSSI: {data.rssi} dBm)")
            self.data_callback(decoded, data.rssi)

    def send(self, message):
        if self.mode == RadioMode.FSK:
            self._send_fsk(message)
            sleep(_FSK_FRAME_AIR_TIME_S)
            self._force_rx()
        elif self.mode == RadioMode.LORA:
            self._send_lora(message)
            sleep(0.1)
            self.lora_handler.set_mode_rx()

    def _send_fsk(self, message):
        print(f"Sending FSK message: {message}")
        self.fsk_handler.send_fsk(message)

    def _send_lora(self, message):
        print(f"Sending LoRa message: {message}")
        self.lora_handler.send(message, 98)

    def cleanup(self):
        if self.mode == RadioMode.FSK:
            self.fsk_handler.close()
        elif self.mode == RadioMode.LORA:
            self.lora_handler.close()