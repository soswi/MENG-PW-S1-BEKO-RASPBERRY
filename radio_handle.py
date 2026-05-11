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

_REG_01_OP_MODE = 0x01
_REG_00_FIFO    = 0x00
_REG_13_IRQ2    = 0x13
_MODE_SLEEP     = 0x00
_MODE_STDBY     = 0x01
_MODE_RXCONT    = 0x05


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
        Force SX1276 into FSK RX continuous regardless of current state.

        Bypasses all driver guards by writing directly to the register
        and setting _mode manually AFTER the register write, so the guard
        in set_mode_rx_fsk() cannot block the transition.

        Sequence:
          1. Write STDBY to register directly
          2. Drain FIFO (read until PayloadReady clears or 64 bytes done)
          3. Write RX continuous to register directly
          4. Sync _mode flag to match reality
          5. Re-configure DIO mapping via SX1276SetRx_fsk() without
             the mode-switch (we already did it)
        """
        with self.fsk_handler._hw_lock:
            # 1. Force STDBY
            self._spi_w(_REG_01_OP_MODE, _MODE_STDBY)
            sleep(0.010)

            # 2. Drain FIFO — read until PayloadReady=0 or 64 bytes
            for _ in range(64):
                if not (self._spi_r(_REG_13_IRQ2) & 0x04):
                    break
                self._spi_r(_REG_00_FIFO)

            # 3. Force RX continuous directly into register
            self._spi_w(_REG_01_OP_MODE, _MODE_RXCONT)
            sleep(0.010)

            # 4. Sync driver's internal flag — AFTER we wrote the register
            #    so no interrupt thread can set it back in between
            self.fsk_handler._mode = _MODE_RXCONT

        # 5. Re-apply DIO mapping (needed for PayloadReady→DIO0 interrupt)
        #    Call SX1276SetRx_fsk but skip its set_mode_rx_fsk() since
        #    we already set the mode. We achieve this by temporarily setting
        #    _mode = RXCONTINUOUS so set_mode_rx_fsk guard skips the write
        #    but DIO mapping registers are still updated.
        #    Actually SX1276SetRx_fsk does DIO mapping THEN calls set_mode_rx_fsk.
        #    The DIO mapping writes happen unconditionally — so just call it.
        #    set_mode_rx_fsk guard will see _mode==RXCONTINUOUS and skip — good,
        #    because we already wrote the register directly above.
        self.fsk_handler.SX1276SetRx_fsk()

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
