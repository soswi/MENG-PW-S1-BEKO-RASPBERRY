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
            self._enter_fsk_rx()
            mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
            print(f"  [INIT] FSK ready — hw_mode=0x{mode:02X} (bits={mode & 0x07})")

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

    def _enter_fsk_rx(self):
        """
        Write SLEEP (0x00) to RegOpMode before calling SX1276SetRx_fsk().

        SX1276SetModem() uses RF_OPMODE_MASK=0xF8 which preserves bit 3
        (LowFrequencyModeOn). After hardware reset the chip defaults to 0x09
        so LowFreqMode stays set through the entire init sequence. With
        LowFreqMode=1 the synthesiser uses the low-band (< 525 MHz) PLL path
        and cannot lock at 868 MHz — leaving the chip stuck at FSRx (0x04).

        Writing 0x00 directly clears bit 3 so set_mode_rx_fsk() reads 0x00
        and writes 0x05 (no LowFreqMode bit), which is correct for 868 MHz.
        """
        self.fsk_handler._spi_write(_REG_01_OP_MODE, _MODE_SLEEP)  # clear LowFreqMode
        self.fsk_handler._mode = _MODE_SLEEP
        self.fsk_handler.SX1276SetRx_fsk()

    def _hw_reset(self):
        GPIO.setup(radio_defines.RESET_PIN, GPIO.OUT)
        GPIO.output(radio_defines.RESET_PIN, GPIO.LOW)
        sleep(0.010)
        GPIO.output(radio_defines.RESET_PIN, GPIO.HIGH)
        sleep(0.010)
        mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
        print(f"  [HW_RESET] done — hw_mode=0x{mode:02X} (bits={mode & 0x07})")

    def _reinit_fsk_rx(self):
        print("  [REINIT] --- begin ---")

        GPIO.remove_event_detect(radio_defines.INTERRUPT_PIN)
        mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
        print(f"  [REINIT] event_detect removed — hw=0x{mode:02X} sw_mode={self.fsk_handler._mode}")

        self._hw_reset()

        self.fsk_handler._mode = _MODE_SLEEP

        self.fsk_handler.SX1276Init()
        mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
        print(f"  [REINIT] after SX1276Init   — hw=0x{mode:02X}")

        self.fsk_handler.SX1276SetChannel()
        self.fsk_handler.SX1276SetTxConfig(fixLEN=radio_defines.FSK_FIX_LEN)
        self.fsk_handler.SX1276SetRxConfig(
            fixLEN=radio_defines.FSK_FIX_LEN,
            payload_len=radio_defines.FSK_PAYLOAD_LEN,
        )
        mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
        print(f"  [REINIT] after SetRxConfig  — hw=0x{mode:02X} (LowFreqMode={(mode >> 3) & 1})")

        self._enter_fsk_rx()
        mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
        print(f"  [REINIT] after _enter_fsk_rx — hw=0x{mode:02X} sw={self.fsk_handler._mode}")

        mode_ok = False
        for i in range(25):
            sleep(0.004)
            mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
            print(f"  [REINIT] poll[{i:02d}] hw=0x{mode:02X} bits={mode & 0x07}")
            if (mode & 0x07) == _MODE_RXCONT:
                mode_ok = True
                break

        GPIO.add_event_detect(radio_defines.INTERRUPT_PIN, GPIO.RISING,
                              callback=self.fsk_handler._handle_interrupt)

        mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
        print(f"  [REINIT] --- end: hw=0x{mode:02X} poll={'OK' if mode_ok else 'TIMEOUT'} ---")

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
                print(f"  [RECV] FSK payload len={len(data)} RSSI={rssi} idx={index}")
                self.data_callback(decoded, rssi, index)
            else:
                print("  [RECV] FSK empty/noise frame")
        elif self.mode == RadioMode.LORA:
            int_data = [int(b) for b in data.message]
            int_data.insert(0, data.header_flags)
            int_data.insert(0, data.header_id)
            int_data.insert(0, data.header_from)
            int_data.insert(0, data.header_to)
            decoded = ''.join(chr(b) for b in int_data)
            print(f"  [RECV] LoRa RSSI={data.rssi} dBm")
            self.data_callback(decoded, data.rssi)

    def send(self, message):
        if self.mode == RadioMode.FSK:
            mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
            print(f"  [SEND] pre-TX hw=0x{mode:02X} sw_mode={self.fsk_handler._mode}")
            self._send_fsk(message)
            mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
            print(f"  [SEND] post-TX hw=0x{mode:02X}")
            sleep(_FSK_FRAME_AIR_TIME_S)
            mode = self.fsk_handler._spi_read(_REG_01_OP_MODE)
            print(f"  [SEND] post-air-time hw=0x{mode:02X} sw_mode={self.fsk_handler._mode}")
            self._reinit_fsk_rx()
        elif self.mode == RadioMode.LORA:
            self._send_lora(message)
            sleep(0.1)
            self.lora_handler.set_mode_rx()

    def _send_fsk(self, message):
        print(f"  [SEND] FSK tx")
        self.fsk_handler.send_fsk(message)

    def _send_lora(self, message):
        print(f"  [SEND] LoRa tx")
        self.lora_handler.send(message, 98)

    def cleanup(self):
        if self.mode == RadioMode.FSK:
            self.fsk_handler.close()
        elif self.mode == RadioMode.LORA:
            self.lora_handler.close()