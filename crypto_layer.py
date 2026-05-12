"""
crypto_layer.py
===============
AES-128-CTR + HMAC-SHA256 cryptographic layer for BEKO radio protocol.
"""

import struct
from Crypto.Cipher import AES
from Crypto.Hash  import HMAC, SHA256

from beko_protocol import AES_KEY, BEKO_PAYLOAD_SIZE, ENCRYPTED_SIZE


CRYPTO_IV_SIZE  = 16
CRYPTO_MIC_SIZE = 4


class CryptoLayer:
    def __init__(self, key: bytes = AES_KEY):
        if not isinstance(key, (bytes, bytearray)):
            raise TypeError("key must be bytes")
        if len(key) != 16:
            raise ValueError("key must be exactly 16 bytes (AES-128)")

        self._key = bytes(key)
        self.tx_counter      = 0
        self.rx_counter_last = 0xFFFFFFFF

    @staticmethod
    def _counter_to_iv(counter: int) -> bytes:
        return struct.pack('>I', counter & 0xFFFFFFFF) + b'\x00' * 12

    @staticmethod
    def _iv_to_counter(iv: bytes) -> int:
        return struct.unpack('>I', iv[:4])[0]

    def _compute_mic(self, iv: bytes, ciphertext: bytes) -> bytes:
        h = HMAC.new(self._key, digestmod=SHA256)
        h.update(iv)
        h.update(ciphertext)
        return h.digest()[:CRYPTO_MIC_SIZE]

    def encrypt(self, plaintext: bytes) -> bytes:
        if not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("plaintext must be bytes")
        if len(plaintext) != BEKO_PAYLOAD_SIZE:
            raise ValueError(
                f"plaintext must be exactly {BEKO_PAYLOAD_SIZE} bytes, "
                f"got {len(plaintext)}"
            )

        iv         = self._counter_to_iv(self.tx_counter)
        cipher     = AES.new(self._key, AES.MODE_CTR, nonce=iv[:8])
        ciphertext = cipher.encrypt(bytes(plaintext))
        mic        = self._compute_mic(iv, ciphertext)

        self.tx_counter = (self.tx_counter + 1) & 0xFFFFFFFF

        result = iv + ciphertext + mic
        assert len(result) == ENCRYPTED_SIZE
        return result

    def decrypt(self, encrypted: bytes) -> bytes:
        if not isinstance(encrypted, (bytes, bytearray)):
            raise TypeError("encrypted must be bytes")
        if len(encrypted) != ENCRYPTED_SIZE:
            raise ValueError(
                f"encrypted blob must be exactly {ENCRYPTED_SIZE} bytes, "
                f"got {len(encrypted)}"
            )

        iv         = encrypted[:CRYPTO_IV_SIZE]
        mic        = encrypted[-CRYPTO_MIC_SIZE:]
        ciphertext = encrypted[CRYPTO_IV_SIZE:-CRYPTO_MIC_SIZE]

        computed_mic = self._compute_mic(iv, ciphertext)
        if computed_mic != mic:
            raise ValueError(
                f"HMAC verification failed — tampering detected "
                f"(computed={computed_mic.hex()} received={mic.hex()})"
            )

        counter = self._iv_to_counter(iv)
        if self.rx_counter_last != 0xFFFFFFFF:
            delta = (counter - self.rx_counter_last) & 0xFFFFFFFF
            if delta == 0 or delta > 15:
                raise ValueError(
                    f"Replay/window check failed "
                    f"(ctr={counter}, last={self.rx_counter_last}, delta={delta})"
                )

        cipher    = AES.new(self._key, AES.MODE_CTR, nonce=iv[:8])
        plaintext = cipher.decrypt(ciphertext)

        self.rx_counter_last = counter
        return plaintext

    def self_test(self) -> bool:
        print("\n=== CRYPTO SELF-TEST ===")

        from beko_frame import build_cmd_payload
        from beko_protocol import CMD_OP_ABSOLUTE

        crypto = CryptoLayer(self._key)
        payload = build_cmd_payload(CMD_OP_ABSOLUTE, 90)

        enc = crypto.encrypt(payload)
        assert len(enc) == ENCRYPTED_SIZE
        assert isinstance(enc, bytes)
        print(f"  [1] encrypt OK — {len(enc)} bytes")

        dec = crypto.decrypt(enc)
        assert dec == payload
        print("  [2] decrypt roundtrip OK")

        tampered = bytearray(enc)
        tampered[-1] ^= 0xFF
        try:
            crypto.decrypt(bytes(tampered))
            raise AssertionError("should have raised ValueError on tampered MIC")
        except ValueError as e:
            assert "HMAC" in str(e)
        print("  [3] tampering detection OK")

        crypto2 = CryptoLayer(self._key)
        enc_a = crypto2.encrypt(build_cmd_payload(CMD_OP_ABSOLUTE, 10))
        enc_b = crypto2.encrypt(build_cmd_payload(CMD_OP_ABSOLUTE, 20))
        crypto2.decrypt(enc_b)
        try:
            crypto2.decrypt(enc_a)
            raise AssertionError("should have raised ValueError on replay")
        except ValueError as e:
            assert "Replay" in str(e) or "window" in str(e)
        print("  [4] replay detection OK")

        print("=== ALL CRYPTO TESTS PASSED ===\n")
        return True


if __name__ == "__main__":
    CryptoLayer().self_test()