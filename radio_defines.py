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

from pyLoraRFM9x.constants import ModemConfig

SPI_PORT = 0
SPI_CHANNEL = 1
INTERRUPT_PIN = 22
INTERRUPT_PIN1 = 23
INTERRUPT_PIN2 = 24
RESET_PIN = 25

FSK_FREQ = 868.0
FSK_TX_POWER = 17
FSK_FIX_LEN = 0
FSK_PAYLOAD_LEN = 255

LORA_FREQ = 868.0
LORA_MODEM_CONFIG = ModemConfig.Bw125Cr45Sf128
LORA_SYNC_WORD = 0x12
LORA_POWER = 0
LORA_ACKS = False
LORA_ADDR = 2