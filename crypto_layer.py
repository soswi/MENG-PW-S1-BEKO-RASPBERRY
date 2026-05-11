"""
crypto_layer.py
===============
AES-128-CTR + HMAC-SHA256 cryptographic layer for BEKO radio protocol.

Compatible with STM32 CMOX implementation — identical algorithm, identical
wire format.  Both sides must use the same AES_KEY from beko_protocol.py.

Wire format of the encrypted blob (always 32 bytes):
    data[0:16]   IV          — 16 bytes
    data[16:28]  Ciphertext  — 12 bytes (AES-128-CTR of 12-byte plaintext)
    data[28:32]  MIC         — 4 bytes (first 4 bytes of HMAC-SHA256)

Verification order (CRITICAL — do not change):
    1. Verify HMAC
    2. Replay-window check (extract counter from IV plaintext)
    3. AES-CTR decrypt

Data is never decrypted before authentication.

Counter / replay-window rules (matching STM32):
    - Sentinel rx_counter_last = 0xFFFFFFFF → accept any first frame
    - After first frame: accept if 1 ≤ delta ≤ 15
      where delta = (received_counter − last_accepted) mod 2^32
    - Counter is a 32-bit value encoded big-endian in IV bytes 0–3

Interface:
    crypto = CryptoLayer(key)
    encrypted_bytes = crypto.encrypt(plaintext_bytes)   # bytes → bytes
    plaintext_bytes = crypto.decrypt(encrypted_bytes)   # bytes → bytes

radio_handle.py passes and returns str (latin-1 encoded bytes).
The conversion happens in radio_controller.py, NOT here.
CryptoLayer works only with bytes internally.
"""

import struct
from Crypto.Cipher import AES
from Crypto.Hash  import HMAC, SHA256

from beko_protocol import AES_KEY, BEKO_PAYLOAD_SIZE, ENCRYPTED_SIZE


CRYPTO_IV_SIZE  = 16
CRYPTO_MIC_SIZE = 4
# CT size = BEKO_PAYLOAD_SIZE = 12
# Encrypted blob = IV(16) + CT(12) + MIC(4) = 32


class CryptoLayer:
    """
    AES-128-CTR + HMAC-SHA256 encryption/decryption.

    One instance per session.  Do not re-instantiate mid-session — it
    resets the counters and breaks replay protection.
    """

    def __init__(self, key: bytes = AES_KEY):
        if not isinstance(key, (bytes, bytearray)):
            raise TypeError("key must be bytes")
        if len(key) != 16:
            raise ValueError("key must be exactly 16 bytes (AES-128)")

        self._key = bytes(key)
        self.tx_counter      = 0
        self.rx_counter_last = 0xFFFFFFFF  # sentinel: accept any first frame

    # ------------------------------------------------------------------ #
    # IV helpers                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _counter_to_iv(counter: int) -> bytes:
        """IV = counter (4 B big-endian) + 12 zero bytes."""
        return struct.pack('>I', counter & 0xFFFFFFFF) + b'\x00' * 12

    @staticmethod
    def _iv_to_counter(iv: bytes) -> int:
        return struct.unpack('>I', iv[:4])[0]

    # ------------------------------------------------------------------ #
    # HMAC                                                                #
    # ------------------------------------------------------------------ #

    def _compute_mic(self, iv: bytes, ciphertext: bytes) -> bytes:
        h = HMAC.new(self._key, digestmod=SHA256)
        h.update(iv)
        h.update(ciphertext)
        return h.digest()[:CRYPTO_MIC_SIZE]

    # ------------------------------------------------------------------ #
    # Public interface                                                    #
    # ------------------------------------------------------------------ #

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypt exactly BEKO_PAYLOAD_SIZE (12) bytes of plaintext.

        Args:
            plaintext: 12 bytes of payload (zero-padded by caller)

        Returns:
            bytes: 32-byte encrypted blob (IV + CT + MIC)

        Raises:
            ValueError: if plaintext is not exactly 12 bytes
        """
        if not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("plaintext must be bytes")
        if len(plaintext) != BEKO_PAYLOAD_SIZE:
            raise ValueError(
                f"plaintext must be exactly {BEKO_PAYLOAD_SIZE} bytes "
                f"(use build_cmd_payload / build_ack_payload), got {len(plaintext)}"
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
        """
        Decrypt a 32-byte encrypted blob received from STM32.

        Verification order: HMAC → replay window → AES decrypt.

        Args:
            encrypted: 32 bytes (IV + CT + MIC) from BekoFrame.data

        Returns:
            bytes: 12-byte plaintext

        Raises:
            ValueError: HMAC failure, replay/window violation, or bad length
        """
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

        # 1. Verify HMAC — before anything else
        computed_mic = self._compute_mic(iv, ciphertext)
        if computed_mic != mic:
            raise ValueError(
                f"HMAC verification failed — tampering detected "
                f"(computed={computed_mic.hex()} received={mic.hex()})"
            )

        # 2. Replay-window check
        #    Sentinel 0xFFFFFFFF means first frame — accept any counter.
        #    After that: 1 ≤ delta ≤ 15
        counter = self._iv_to_counter(iv)
        if self.rx_counter_last != 0xFFFFFFFF:
            delta = (counter - self.rx_counter_last) & 0xFFFFFFFF
            if delta == 0 or delta > 15:
                raise ValueError(
                    f"Replay/window check failed "
                    f"(ctr={counter}, last={self.rx_counter_last}, delta={delta})"
                )

        # 3. AES-128-CTR decrypt
        cipher    = AES.new(self._key, AES.MODE_CTR, nonce=iv[:8])
        plaintext = cipher.decrypt(ciphertext)

        self.rx_counter_last = counter
        return plaintext

    # ------------------------------------------------------------------ #
    # Self-test                                                           #
    # ------------------------------------------------------------------ #

    def self_test(self) -> bool:
        """
        Verify encrypt→decrypt roundtrip, tampering detection, and
        replay detection.  Called once on startup.

        Returns:
            True on success, raises AssertionError on failure.
        """
        print("\n=== CRYPTO SELF-TEST ===")

        from beko_frame import build_cmd_payload
        from beko_protocol import CMD_OP_ABSOLUTE

        crypto = CryptoLayer(self._key)
        payload = build_cmd_payload(CMD_OP_ABSOLUTE, 90)   # 12 bytes

        # [1] Encrypt
        enc = crypto.encrypt(payload)
        assert len(enc) == ENCRYPTED_SIZE, f"enc len {len(enc)}"
        assert isinstance(enc, bytes)
        print(f"  [1] encrypt OK — {len(enc)} bytes")

        # [2] Decrypt
        dec = crypto.decrypt(enc)
        assert dec == payload, f"roundtrip mismatch\n  orig={payload.hex()}\n  dec={dec.hex()}"
        print("  [2] decrypt roundtrip OK")

        # [3] Tampering detection — flip one MIC byte
        tampered = bytearray(enc)
        tampered[-1] ^= 0xFF
        try:
            crypto.decrypt(bytes(tampered))
            raise AssertionError("should have raised ValueError on tampered MIC")
        except ValueError as e:
            assert "HMAC" in str(e), f"unexpected error: {e}"
        print("  [3] tampering detection OK")

        # [4] Replay detection — re-decrypt already-accepted frame
        #     Need a fresh counter state: encrypt a second frame, then
        #     try to replay the first one (counter too small).
        crypto2 = CryptoLayer(self._key)
        enc_a = crypto2.encrypt(build_cmd_payload(CMD_OP_ABSOLUTE, 10))
        enc_b = crypto2.encrypt(build_cmd_payload(CMD_OP_ABSOLUTE, 20))
        crypto2.decrypt(enc_b)   # accept counter=1
        try:
            crypto2.decrypt(enc_a)  # replay counter=0 → delta=0xFFFFFFFF > 15
            raise AssertionError("should have raised ValueError on replay")
        except ValueError as e:
            assert "Replay" in str(e) or "window" in str(e), f"unexpected error: {e}"
        print("  [4] replay detection OK")

        print("=== ALL CRYPTO TESTS PASSED ===\n")
        return True


if __name__ == "__main__":
    CryptoLayer().self_test()
