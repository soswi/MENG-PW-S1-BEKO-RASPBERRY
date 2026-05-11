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
import spidev as _spidev

# Air time for one 41-byte BEKO frame at 4800 bps ≈ 73 ms + margin
_FSK_FRAME_AIR_TIME_S = 0.130

# SX1276 registers needed for manual FIFO flush
_REG_01_OP_MODE   = 0x01
_REG_00_FIFO      = 0x00
_REG_0D_RXCONFIG  = 0x0D
_REG_13_IRQ2      = 0x13

_RF_OPMODE_MASK   = 0xF8
_MODE_SLEEP       = 0x00
_MODE_STDBY       = 0x01
_MODE_RXCONT      = 0x05   # FSK RX continuous (without LoRa bit)

# RestartRxWithPllLock bit in RegRxConfig
_RF_RESTART_RX_WITH_PLL = 0x20


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
            # Keep a direct SPI reference for register-level operations
            # (same bus/channel as the driver uses)
            self._spi = self.fsk_handler.spi

            self.fsk_handler.on_recv = self.handle_received_data
            self._enter_rx()

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
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _spi_r(self, reg):
        return self._spi.xfer([reg & 0x7F, 0x00])[1]

    def _spi_w(self, reg, val):
        self._spi.xfer([reg | 0x80, val])

    def _enter_rx(self):
        """
        Proper TX→RX (or any→RX) sequence for SX1276 FSK mode.

        Per SX1276 datasheet section 4.2.5:
          1. Go to STDBY (required intermediate state — PLL re-locks here)
          2. Wait for PLL to settle
          3. Issue RestartRx to flush FIFO and clear PayloadReady
          4. Go to RX continuous

        Skipping STDBY causes the chip to stall in FSRX (0x04).
        Not flushing FIFO keeps PayloadReady=True which prevents new
        interrupts from firing on DIO0.
        """
        with self.fsk_handler._hw_lock:
            # 1. STDBY
            self._spi_w(_REG_01_OP_MODE, _MODE_STDBY)
            self.fsk_handler._mode = 0x01  # MODE_STDBY
            sleep(0.010)  # 10 ms — PLL settling time per datasheet

            # 2. Flush FIFO: RestartRxWithPllLock clears FIFO and
            #    deasserts PayloadReady without leaving RX mode.
            #    Write to RegRxConfig bit 5.
            rxcfg = self._spi_r(_REG_0D_RXCONFIG)
            self._spi_w(_REG_0D_RXCONFIG, rxcfg | _RF_RESTART_RX_WITH_PLL)
            sleep(0.005)

        # 3. Full RX re-arm via driver (sets DIO mapping + RX continuous)
        self.fsk_handler.SX1276SetRx_fsk()

        # 4. Verify
        mode = self._spi_r(_REG_01_OP_MODE)
        irq2 = self._spi_r(_REG_13_IRQ2)
        print(f"  [RX] mode=0x{mode:02X}  PayloadReady={bool(irq2 & 0x04)}")

    # ------------------------------------------------------------------ #

    def start_rx(self):
        if self.mode == RadioMode.FSK:
            self._enter_rx()
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
        """
        TX → wait for air time → STDBY → RX.
        """
        if self.mode == RadioMode.FSK:
            self._send_fsk(message)
            sleep(_FSK_FRAME_AIR_TIME_S)
            self._enter_rx()
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
