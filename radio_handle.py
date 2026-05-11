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


# Air time for one 41-byte BEKO frame at 4800 bps:
#   (1 length byte + 41 payload + 2 CRC) * 8 bits / 4800 bps ≈ 73 ms
# Add margin for preamble and PLL settling.
_FSK_FRAME_AIR_TIME_S = 0.130


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

    def start_rx(self):
        """
        Switch SX1276 to FSK RX continuous mode.

        SX1276 datasheet requires TX → STDBY → RX, not TX → RX directly.
        Skipping STDBY causes the PLL to stall in FSRX (0x04) and never
        reach RX continuous (0x05).  We also flush any stale PayloadReady
        by going through set_mode_idle() (STDBY) first.
        """
        if self.mode == RadioMode.FSK:
            # Step 1: STDBY — required intermediate state per SX1276 datasheet.
            # Also resets the internal mode flag so set_mode_rx_fsk() guard passes.
            self.fsk_handler.set_mode_idle()
            sleep(0.005)   # 5 ms for PLL to settle in STDBY

            # Step 2: Re-arm RX (configures DIO mapping + starts RX continuous)
            self.fsk_handler.SX1276SetRx_fsk()

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
        Transmit message then return to RX via STDBY.

        Sequence: TX → wait air time → STDBY → RX continuous.
        The STDBY step is mandatory for SX1276 PLL to re-lock on RX freq.
        """
        if self.mode == RadioMode.FSK:
            self._send_fsk(message)
            # Wait for frame to finish transmitting before switching modes.
            # 130 ms covers 73 ms air time + preamble + margin.
            sleep(_FSK_FRAME_AIR_TIME_S)
            self.start_rx()   # goes through STDBY internally

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
