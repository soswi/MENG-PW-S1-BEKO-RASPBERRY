"""
============================================================================
crypto_layer.py
============================================================================
Moduł szyfrowania dla systemu BEKO (Raspberry Pi)

Kompatybilny z interfejsem klasy FSK:
    encrypted_msg = crypto.encrypt(message)
    message = crypto.decrypt(encrypted_msg)

Wewnętrznie:
    - AES-128-CTR (pycryptodome)
    - HMAC-SHA256[:4] (authentication)
    - Replay protection (frame counter)

Frame format (na radio):
    [IV: 16B] [Ciphertext: len(message)] [MIC: 4B]

Konwersja radio_handle:
    - send:    bytes → str (przez chr()) przed wysłaniem
    - receive: str → bytes (przez ord()) po odebraniu
    Crypto layer obsługuje obie konwersje transparentnie.

============================================================================
"""

from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256
import struct


class CryptoLayer:
    """
    Moduł szyfrowania: AES-128-CTR + HMAC-SHA256

    Interfejs kompatybilny z radio_handle (FSK/LoRa):
        encrypted_str = crypto.encrypt(plaintext)   # zwraca str dla radio_handle.send()
        plaintext_str = crypto.decrypt(encoded_str) # przyjmuje str z data_callback
    """

    CRYPTO_KEY_SIZE = 16
    CRYPTO_MIC_SIZE = 4
    CRYPTO_MAX_DATA = 256

    def __init__(self, key):
        """
        Inicjalizacja warstwy kryptografii

        Args:
            key: 16-bajtowy klucz AES-128 (bytes)
        """
        if not isinstance(key, (bytes, bytearray)):
            raise TypeError("Key must be bytes")
        if len(key) != self.CRYPTO_KEY_SIZE:
            raise ValueError(f"Key must be {self.CRYPTO_KEY_SIZE} bytes")

        self.key = bytes(key)
        self.tx_counter = 0
        self.rx_counter_last = 0xFFFFFFFF

        print(f"INFO: Crypto initialized")

    # ------------------------------------------------------------------ #
    #  Helpers: konwersja str <-> bytes (kompatybilność z radio_handle)   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _bytes_to_radio_str(data: bytes) -> str:
        """
        Konwertuj bytes → str w sposób kompatybilny z radio_handle.
        radio_handle.handle_received_data robi: chr(elem) for elem in data
        Więc send musi dostarczyć coś co po tej konwersji da oryginalne bajty.
        Używamy latin-1: bajt N → chr(N), odwracalne przez ord(c).
        """
        return data.decode('latin-1')

    @staticmethod
    def _radio_str_to_bytes(data: str) -> bytes:
        """
        Konwertuj str → bytes — odwrócenie konwersji z handle_received_data.
        handle_received_data: ''.join(chr(elem) for elem in raw_bytes)
        Odwrócenie:           bytes(ord(c) for c in string)
        """
        return bytes(ord(c) for c in data)

    # ------------------------------------------------------------------ #
    #  Wewnętrzna kryptografia                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _counter_to_iv(counter):
        """IV = [counter (4B big-endian)] + [zeros (12B)]"""
        return struct.pack('>I', counter & 0xFFFFFFFF) + b'\x00' * 12

    @staticmethod
    def _iv_to_counter(iv):
        return struct.unpack('>I', iv[:4])[0]

    def _compute_hmac(self, iv, ciphertext):
        """HMAC-SHA256(IV || ciphertext)[:4]"""
        hmac = HMAC.new(self.key, digestmod=SHA256)
        hmac.update(iv)
        hmac.update(ciphertext)
        return hmac.digest()[:self.CRYPTO_MIC_SIZE]

    # ------------------------------------------------------------------ #
    #  Publiczny interfejs                                                 #
    # ------------------------------------------------------------------ #

    def encrypt(self, plaintext) -> str:
        """
        Szyfruj dane i zwróć str gotowy do radio_handle.send().

        Args:
            plaintext: dane do szyfrowania (bytes, bytearray lub str)

        Returns:
            str: zaszyfrowana ramka zakodowana jako latin-1 string
                 Format wewnętrzny: [IV (16B)] + [ciphertext (N)] + [MIC (4B)]

        Raises:
            ValueError: jeśli dane są za duże
        """
        # Normalizacja wejścia
        if isinstance(plaintext, str):
            plaintext = plaintext.encode('utf-8')
        else:
            plaintext = bytes(plaintext)

        if len(plaintext) == 0 or len(plaintext) > self.CRYPTO_MAX_DATA:
            raise ValueError(f"Plaintext must be 1-{self.CRYPTO_MAX_DATA} bytes")

        # Szyfrowanie
        iv = self._counter_to_iv(self.tx_counter)
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=iv[:8])
        ciphertext = cipher.encrypt(plaintext)
        mic = self._compute_hmac(iv, ciphertext)

        self.tx_counter = (self.tx_counter + 1) & 0xFFFFFFFF

        encrypted_bytes = iv + ciphertext + mic

        print(f"ENCRYPT: {len(plaintext)} bytes → {len(encrypted_bytes)} bytes "
              f"(ctr={self.tx_counter - 1})")

        # Zwróć jako str gotowy dla radio_handle.send()
        return self._bytes_to_radio_str(encrypted_bytes)

    def decrypt(self, encrypted_msg) -> bytes:
        """
        Odszyfruj dane z radio_handle data_callback.

        Args:
            encrypted_msg: str z data_callback (latin-1 encoded)
                           lub bytes (dla bezpośredniego użycia)

        Returns:
            bytes: odszyfrowane dane

        Raises:
            ValueError: jeśli HMAC verification failed lub replay attack
        """
        # Normalizacja wejścia — obsługa str z radio_handle
        if isinstance(encrypted_msg, str):
            encrypted_msg = self._radio_str_to_bytes(encrypted_msg)
        else:
            encrypted_msg = bytes(encrypted_msg)

        # Min: 16 (IV) + 1 (min CT) + 4 (MIC) = 21 bajtów
        if len(encrypted_msg) < 21:
            raise ValueError(
                f"Encrypted message too short: {len(encrypted_msg)} bytes (min 21)")

        # Rozpakuj
        iv = encrypted_msg[:16]
        mic = encrypted_msg[-4:]
        ciphertext = encrypted_msg[16:-4]

        # Weryfikacja HMAC
        computed_mic = self._compute_hmac(iv, ciphertext)
        if computed_mic != mic:
            print(f"ERROR: HMAC verification failed!")
            print(f"  Computed: {computed_mic.hex().upper()}")
            print(f"  Received: {mic.hex().upper()}")
            raise ValueError("HMAC verification failed - tampering detected!")

        # Weryfikacja replay
        counter = self._iv_to_counter(iv)
        if counter <= self.rx_counter_last:
            print(f"ERROR: Replay attack detected! (ctr={counter}, last={self.rx_counter_last})")
            raise ValueError("Replay attack detected!")

        # Odszyfrowanie
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=iv[:8])
        plaintext = cipher.decrypt(ciphertext)

        self.rx_counter_last = counter

        print(f"DECRYPT: {len(ciphertext)} bytes → {len(plaintext)} bytes, ctr={counter}")

        return plaintext

    def self_test(self):
        """Prosty self-test"""
        print("\n=== CRYPTO SELF-TEST ===")

        key = bytes.fromhex("AE6852F8121067CC4BF7A5765577F39E")
        plaintext = b'Test message!!!!'

        crypto = CryptoLayer(key)

        print("[1] Encrypt → str...")
        encrypted_str = crypto.encrypt(plaintext)
        assert isinstance(encrypted_str, str), "encrypt() powinno zwracać str"
        print(f"    Typ: {type(encrypted_str)}, długość: {len(encrypted_str)}")

        print("[2] Decrypt ← str...")
        decrypted = crypto.decrypt(encrypted_str)

        print("[3] Compare...")
        if plaintext == decrypted:
            print("✓ SUCCESS: encrypt→decrypt roundtrip OK")
        else:
            print(f"✗ FAILED\n  Original:  {plaintext}\n  Decrypted: {decrypted}")
            return False

        print("[4] Tampering detection...")
        # Zepsuj MIC w stringu
        b = bytearray(encrypted_str.encode('latin-1'))
        b[-4] ^= 0xFF
        tampered_str = b.decode('latin-1')
        try:
            crypto.decrypt(tampered_str)
            print("✗ Powinno wykryć manipulację")
            return False
        except ValueError as e:
            if "HMAC" in str(e):
                print("✓ Tampering detected")
            else:
                print(f"✗ Zły błąd: {e}")
                return False

        print("[5] Replay detection...")
        crypto.encrypt(b'Another msg!!!!!')
        try:
            crypto.decrypt(encrypted_str)
            print("✗ Powinno wykryć replay")
            return False
        except ValueError as e:
            if "Replay" in str(e):
                print("✓ Replay detected")
            else:
                print(f"✗ Zły błąd: {e}")
                return False

        print("\n=== ALL TESTS PASSED ===\n")
        return True


if __name__ == "__main__":
    key = bytes.fromhex("AE6852F8121067CC4BF7A5765577F39E")
    crypto = CryptoLayer(key)
    crypto.self_test()