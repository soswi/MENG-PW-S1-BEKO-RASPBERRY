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
        Force SX1276 into FSK RX continuous regardless of current state.

        Root cause of the missed-TELEM bug: the old code called
        SX1276SetRx_fsk() AFTER entering RXCONT.  That call writes to
        RXCONFIG which triggers a receiver restart (hardware briefly drops
        to FSRx/0x04).  If the TELEM frame completed reception during that
        window, PayloadReady was already high when DIO0 was finally mapped
        to it — no rising edge, no GPIO interrupt, 5 s timeout.

        Fixed sequence:
          1. STDBY  — stop any ongoing reception
          2. Drain FIFO while PayloadReady is set (discard stale data)
          3. Configure DIO0→PayloadReady BEFORE entering RX so the GPIO
             edge detector is armed before the first packet can arrive
          4. Clear driver RX buffers
          5. Set _mode flag so _handle_interrupt() accepts callbacks
          6. Enter RXCONT — interrupt is already armed, no edge can be missed
          7. If PayloadReady is already set after entering RX (packet was in
             the FIFO before the drain, or arrived during the µs transition),
             manually trigger _handle_interrupt() — there will be no rising
             edge on DIO0 for an already-high signal.
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

            # 3. DIO mapping: DIO0→PayloadReady (bits[7:6]=00 in FSK RX).
            #    Done here, in STDBY, BEFORE entering RX.  The radio cannot
            #    receive in STDBY so no packet can arrive between the mapping
            #    write and the mode switch — no edge can be missed.
            self._spi_w(_REG_40_DIOMAP1,
                        (self._spi_r(_REG_40_DIOMAP1) & 0x03) | 0x0C)
            self._spi_w(_REG_41_DIOMAP2,
                        (self._spi_r(_REG_41_DIOMAP2) & 0x3E) | 0xC1)

            # 4. Clear driver RX state
            self.fsk_handler._rx_buffer      = []
            self.fsk_handler._rx_payload_len = 0
            self.fsk_handler._rssi           = 0

            # 5. Sync _mode BEFORE the register write so _handle_interrupt()
            #    accepts callbacks the instant the radio starts receiving.
            self.fsk_handler._mode = _MODE_RXCONT

            # 6. Enter RX continuous
            self._spi_w(_REG_01_OP_MODE, _MODE_RXCONT)
            sleep(0.010)

        mode = self._spi_r(_REG_01_OP_MODE)
        irq2 = self._spi_r(_REG_13_IRQ2)
        print(f"  [RX] mode=0x{mode:02X}  PayloadReady={bool(irq2 & 0x04)}")

        # 7. PayloadReady already high → DIO0 was high before GPIO edge
        #    detector armed (or drain missed a packet).  No rising edge will
        #    fire, so manually trigger the read.
        if irq2 & 0x04:
            self.fsk_handler._handle_interrupt(None)

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