"""
MIT License

Copyright (c) 2024 BEER-TEAM (Piotr Polnau, Jan Sosulski, Piotr Baprawski)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from pyLoraRFM9x import LoRa, ModemConfig
from sx_1276_driver.radio_driver import FSK
import RPi.GPIO as GPIO
import radio_defines
from enum import Enum, auto
from time import sleep


class RadioMode(Enum):
    """
    Enum class for Radio Modes: FSK and LoRa.

    Possible values:
    FSK: Frequency Shift Keying
    LORA: LoRa
    """
    FSK  = auto()
    LORA = auto()

    def __str__(self) -> str:
        return self.name


class RadioHandler:
    def __init__(self, mode, data_callback):
        """
        Initialize the RadioHandler with SPI/GPIO and start receiving.

        :param mode:          RadioMode.FSK or RadioMode.LORA
        :param data_callback: Called with (data_str, rssi, index) on RX
        """
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
            raise ValueError("Invalid mode. Please choose 'fsk' or 'lora'.")

        print(f"{self.mode} handler is running... Waiting for data.")

    # ------------------------------------------------------------------ #

    def start_rx(self):
        """Switch radio back to continuous receive mode."""
        if self.mode == RadioMode.FSK:
            self.fsk_handler.SX1276SetRx_fsk()
        elif self.mode == RadioMode.LORA:
            self.lora_handler.set_mode_rx()

    def handle_received_data(self, data, rssi=None, index=None):
        """Decode raw bytes from driver and forward to data_callback."""
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
        Transmit a message and return only after the radio has finished
        sending and is back in RX mode.

        For FSK @ 4800 bps a 41-byte frame takes ~86 ms on air.
        We call wait_packet_sent() (polls up to 1 s for TX-done interrupt)
        instead of a fixed sleep so we return as soon as TX is actually done.
        """
        if self.mode == RadioMode.FSK:
            self._send_fsk(message)
            # Wait for TX-done interrupt (DIO1 → _handle_interrupt sets
            # self._mode back out of MODE_TX).  Timeout = 1 s (driver default).
            sent = self.fsk_handler.wait_packet_sent()
            if not sent:
                print("WARNING: wait_packet_sent() timed out — forcing RX")
            self.start_rx()

        elif self.mode == RadioMode.LORA:
            self._send_lora(message)
            sleep(0.1)
            self.start_rx()

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
