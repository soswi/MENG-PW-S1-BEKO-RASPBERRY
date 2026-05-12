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
from datetime import datetime

_FSK_FRAME_AIR_TIME_S = 0.130

_REG_01_OP_MODE    = 0x01
_REG_3E_IRQ_FLAGS1 = 0x3E   # bit7=ModeReady  bit4=PllLock  bit6=RxReady
_MODE_SLEEP        = 0x00
_MODE_STDBY        = 0x01
_MODE_RXCONT       = 0x05


def _ts():
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


def _chip_status(fsk):
    """Read and format RegOpMode + RegIrqFlags1 in one call."""
    mode = fsk._spi_read(_REG_01_OP_MODE)
    irq1 = fsk._spi_read(_REG_3E_IRQ_FLAGS1)
    pll  = (irq1 >> 4) & 1
    rdy  = (irq1 >> 7) & 1
    rxrdy = (irq1 >> 6) & 1
    return mode, f"hw=0x{mode:02X}({mode & 0x07}) IrqF1=0x{irq1:02X} PllLock={pll} ModeReady={rdy} RxReady={rxrdy}"


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
            _, s = _chip_status(self.fsk_handler)
            print(f"[{_ts()}] [INIT] FSK ready — {s}")

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

        print(f"[{_ts()}] {self.mode} handler is running... Waiting for data.")

    # ------------------------------------------------------------------ #

    def _enter_fsk_rx(self):
        """
        Explicitly write SLEEP (0x00) then call SX1276SetRx_fsk().

        SX1276SetModem() uses RF_OPMODE_MASK=0xF8 which preserves bit 3
        (LowFrequencyModeOn). For safety we write 0x00 directly so
        set_mode_rx_fsk() always reads a clean value and writes 0x05.
        """
        self.fsk_handler._spi_write(_REG_01_OP_MODE, _MODE_SLEEP)
        self.fsk_handler._mode = _MODE_SLEEP
        self.fsk_handler.SX1276SetRx_fsk()

    def _hw_reset(self):
        GPIO.setup(radio_defines.RESET_PIN, GPIO.OUT)
        GPIO.output(radio_defines.RESET_PIN, GPIO.LOW)
        sleep(0.010)
        GPIO.output(radio_defines.RESET_PIN, GPIO.HIGH)
        sleep(0.010)
        _, s = _chip_status(self.fsk_handler)
        print(f"[{_ts()}] [HW_RESET] done — {s}")

    def _reinit_fsk_rx(self):
        print(f"[{_ts()}] [REINIT] --- begin ---")

        GPIO.remove_event_detect(radio_defines.INTERRUPT_PIN)
        _, s = _chip_status(self.fsk_handler)
        print(f"[{_ts()}] [REINIT] event_detect removed — {s} sw={self.fsk_handler._mode}")

        self._hw_reset()
        self.fsk_handler._mode = _MODE_SLEEP

        self.fsk_handler.SX1276Init()
        _, s = _chip_status(self.fsk_handler)
        print(f"[{_ts()}] [REINIT] after SX1276Init    — {s}")

        self.fsk_handler.SX1276SetChannel()
        self.fsk_handler.SX1276SetTxConfig(fixLEN=radio_defines.FSK_FIX_LEN)
        self.fsk_handler.SX1276SetRxConfig(
            fixLEN=radio_defines.FSK_FIX_LEN,
            payload_len=radio_defines.FSK_PAYLOAD_LEN,
        )
        _, s = _chip_status(self.fsk_handler)
        print(f"[{_ts()}] [REINIT] after SetRxConfig   — {s}")

        self._enter_fsk_rx()
        _, s = _chip_status(self.fsk_handler)
        print(f"[{_ts()}] [REINIT] after _enter_fsk_rx — {s} sw={self.fsk_handler._mode}")

        # Re-arm interrupt HERE — before the poll — so we never miss a PayloadReady.
        GPIO.add_event_detect(radio_defines.INTERRUPT_PIN, GPIO.RISING,
                              callback=self.fsk_handler._handle_interrupt)
        print(f"[{_ts()}] [REINIT] interrupt re-armed — chip now listening")

        # Diagnostic poll only — does NOT block reception.
        mode_ok = False
        for i in range(10):
            sleep(0.004)
            mode, s = _chip_status(self.fsk_handler)
            print(f"[{_ts()}] [REINIT] poll[{i:02d}] {s}")
            if (mode & 0x07) == _MODE_RXCONT:
                mode_ok = True
                break

        _, s = _chip_status(self.fsk_handler)
        print(f"[{_ts()}] [REINIT] --- end poll={'OK(0x05)' if mode_ok else 'scanning(0x04)'} {s} ---")

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
                print(f"[{_ts()}] [RECV] FSK len={len(data)} RSSI={rssi} idx={index}")
                self.data_callback(decoded, rssi, index)
            else:
                print(f"[{_ts()}] [RECV] FSK empty/noise frame")
        elif self.mode == RadioMode.LORA:
            int_data = [int(b) for b in data.message]
            int_data.insert(0, data.header_flags)
            int_data.insert(0, data.header_id)
            int_data.insert(0, data.header_from)
            int_data.insert(0, data.header_to)
            decoded = ''.join(chr(b) for b in int_data)
            print(f"[{_ts()}] [RECV] LoRa RSSI={data.rssi} dBm")
            self.data_callback(decoded, data.rssi)

    def send(self, message):
        if self.mode == RadioMode.FSK:
            _, s = _chip_status(self.fsk_handler)
            print(f"[{_ts()}] [SEND] pre-TX {s} sw={self.fsk_handler._mode}")
            self._send_fsk(message)
            _, s = _chip_status(self.fsk_handler)
            print(f"[{_ts()}] [SEND] post-send_fsk {s}")
            sleep(_FSK_FRAME_AIR_TIME_S)
            _, s = _chip_status(self.fsk_handler)
            print(f"[{_ts()}] [SEND] post-air-time {s} sw={self.fsk_handler._mode}")
            self._reinit_fsk_rx()
        elif self.mode == RadioMode.LORA:
            self._send_lora(message)
            sleep(0.1)
            self.lora_handler.set_mode_rx()

    def _send_fsk(self, message):
        print(f"[{_ts()}] [SEND] FSK tx start")
        self.fsk_handler.send_fsk(message)

    def _send_lora(self, message):
        print(f"[{_ts()}] [SEND] LoRa tx start")
        self.lora_handler.send(message, 98)

    def cleanup(self):
        if self.mode == RadioMode.FSK:
            self.fsk_handler.close()
        elif self.mode == RadioMode.LORA:
            self.lora_handler.close()
