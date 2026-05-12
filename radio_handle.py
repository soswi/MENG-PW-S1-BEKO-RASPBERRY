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
_MODE_SLEEP     = 0x00
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
            self.fsk_handler.on_recv = self.handle_received_data
            self.fsk_handler.SX1276SetRx_fsk()

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

    def _reinit_fsk_rx(self):
        """
        Re-enter FSK RxContinuous after TX by re-running the full chip
        initialisation sequence.  This is the same path taken in __init__
        and is the only reliable way to recover after TX on this chip.
        """
        # Remove event detection so no stale TxDone / PayloadReady callback
        # fires while the chip is being reconfigured.
        GPIO.remove_event_detect(radio_defines.INTERRUPT_PIN)

        # Ensure the driver's mode shadow is not RXCONTINUOUS so that
        # set_mode_rx_fsk()'s guard does not silently skip the mode write.
        self.fsk_handler._mode = _MODE_SLEEP

        # Full re-init — mirrors FSK.__init__ (after GPIO/SPI setup).
        # SX1276SetModem() inside each step writes SLEEP first, so the chip
        # is always in a known quiescent state before the final mode switch.
        self.fsk_handler.SX1276Init()
        self.fsk_handler.SX1276SetChannel()
        self.fsk_handler.SX1276SetTxConfig(fixLEN=radio_defines.FSK_FIX_LEN)
        self.fsk_handler.SX1276SetRxConfig(
            fixLEN=radio_defines.FSK_FIX_LEN,
            payload_len=radio_defines.FSK_PAYLOAD_LEN,
        )
        self.fsk_handler.SX1276SetRx_fsk()

        # Verify the chip reached RxContinuous.
        mode_ok = False
        for _ in range(50):
            sleep(0.002)
            mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
            if (mode & 0x07) == _MODE_RXCONT:
                mode_ok = True
                break

        # Re-arm edge detection now that the chip is stable.
        GPIO.add_event_detect(radio_defines.INTERRUPT_PIN, GPIO.RISING,
                              callback=self.fsk_handler._handle_interrupt)

        mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
        print(f"  [RX] mode=0x{mode:02X} (mode-bits={mode & 0x07}) poll={'OK' if mode_ok else 'TIMEOUT'}")

    # ------------------------------------------------------------------ #

    def start_rx(self):
        if self.mode == RadioMode.FSK:
            self._reinit_fsk_rx()
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
            self._reinit_fsk_rx()
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
