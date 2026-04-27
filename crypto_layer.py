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

============================================================================
"""

from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256
import struct


class CryptoLayer:
    """
    Moduł szyfrowania: AES-128-CTR + HMAC-SHA256
    
    Interfejs kompatybilny z FSK._encrypt/_decrypt()
    
    Użycie:
        crypto = CryptoLayer(key)
        encrypted = crypto.encrypt(plaintext)
        plaintext = crypto.decrypt(encrypted)
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
    
    @staticmethod
    def _counter_to_iv(counter):
        """
        Wygeneruj IV z frame_counter
        IV = [counter (4B big-endian)] + [zeros (12B)]
        """
        iv = struct.pack('>I', counter & 0xFFFFFFFF) + b'\x00' * 12
        return iv
    
    @staticmethod
    def _iv_to_counter(iv):
        """
        Ekstrahuj counter z IV
        """
        return struct.unpack('>I', iv[:4])[0]
    
    def _compute_hmac(self, iv, ciphertext):
        """
        Oblicz HMAC-SHA256(IV || ciphertext)[:4]
        """
        hmac = HMAC.new(self.key, digestmod=SHA256)
        hmac.update(iv)
        hmac.update(ciphertext)
        digest = hmac.digest()
        return digest[:self.CRYPTO_MIC_SIZE]
    
    def encrypt(self, plaintext):
        """
        Szyfruj dane (AES-128-CTR + HMAC-SHA256)
        
        Interfejs kompatybilny z FSK._encrypt():
            encrypted = crypto.encrypt(plaintext)
        
        Args:
            plaintext: dane do szyfrowania (bytes)
        
        Returns:
            bytes: [IV (16B)] + [ciphertext (N)] + [MIC (4B)]
        
        Raises:
            ValueError: jeśli dane są za duże
        """
        if not isinstance(plaintext, (bytes, bytearray)):
            plaintext = bytes(plaintext)
        
        if len(plaintext) == 0 or len(plaintext) > self.CRYPTO_MAX_DATA:
            raise ValueError(f"Plaintext must be 1-{self.CRYPTO_MAX_DATA} bytes")
        
        # Wygeneruj IV
        iv = self._counter_to_iv(self.tx_counter)
        
        # Szyfruj AES-CTR (bez paddingu!)
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=iv[:8])
        ciphertext = cipher.encrypt(plaintext)
        
        # Wylicz HMAC
        mic = self._compute_hmac(iv, ciphertext)
        
        # Inkrementuj counter
        self.tx_counter = (self.tx_counter + 1) & 0xFFFFFFFF
        
        # Zwróć spakowany frame: IV + ciphertext + MIC
        encrypted_msg = iv + ciphertext + mic
        
        print(f"ENCRYPT: {len(plaintext)} bytes → {len(encrypted_msg)} bytes "
              f"(IV={len(iv)}, CT={len(ciphertext)}, MIC={len(mic)}), ctr={self.tx_counter-1}")
        
        return encrypted_msg
    
    def decrypt(self, encrypted_msg):
        """
        Odszyfruj dane (AES-128-CTR) + sprawdź HMAC
        
        Interfejs kompatybilny z FSK._decrypt():
            plaintext = crypto.decrypt(encrypted_msg)
        
        Args:
            encrypted_msg: bytes z formatem [IV (16B)] + [ciphertext (N)] + [MIC (4B)]
        
        Returns:
            bytes: odszyfrowane dane
        
        Raises:
            ValueError: jeśli HMAC verification failed lub replay attack
        """
        if not isinstance(encrypted_msg, (bytes, bytearray)):
            encrypted_msg = bytes(encrypted_msg)
        
        # Min: 16 (IV) + 1 (min CT) + 4 (MIC) = 21 bajtów
        if len(encrypted_msg) < 21:
            raise ValueError(f"Encrypted message too short: {len(encrypted_msg)} bytes (min 21)")
        
        # Rozpakuj
        iv = encrypted_msg[:16]
        mic = encrypted_msg[-4:]
        ciphertext = encrypted_msg[16:-4]
        
        # Sprawdź HMAC
        computed_mic = self._compute_hmac(iv, ciphertext)
        
        if computed_mic != mic:
            print(f"ERROR: HMAC verification failed!")
            print(f"  Computed: {computed_mic.hex().upper()}")
            print(f"  Received: {mic.hex().upper()}")
            raise ValueError("HMAC verification failed - tampering detected!")
        
        # Sprawdź replay
        counter = self._iv_to_counter(iv)
        
        if counter <= self.rx_counter_last:
            print(f"ERROR: Replay attack detected! (ctr={counter}, last={self.rx_counter_last})")
            raise ValueError("Replay attack detected!")
        
        # Odszyfruj AES-CTR
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=iv[:8])
        plaintext = cipher.decrypt(ciphertext)
        
        # Aktualizuj ostatni counter
        self.rx_counter_last = counter
        
        print(f"DECRYPT: {len(ciphertext)} bytes → {len(plaintext)} bytes, ctr={counter}")
        
        return plaintext
    
    def self_test(self):
        """
        Prosty self-test
        """
        print("\n=== CRYPTO SELF-TEST ===")
        
        key = bytes.fromhex("AE6852F8121067CC4BF7A5765577F39E")
        plaintext = b'Test message!!!!'
        
        crypto = CryptoLayer(key)
        
        # Test 1: Encrypt
        print("[1] Encrypt...")
        encrypted = crypto.encrypt(plaintext)
        print(f"    Size: {len(encrypted)} bytes")
        
        # Test 2: Decrypt
        print("[2] Decrypt...")
        decrypted = crypto.decrypt(encrypted)
        
        # Test 3: Compare
        print("[3] Compare...")
        if plaintext == decrypted:
            print("✓ SUCCESS")
        else:
            print("✗ FAILED")
            print(f"  Original:  {plaintext}")
            print(f"  Decrypted: {decrypted}")
            return False
        
        # Test 4: Test tampering
        print("[4] Test tampering detection...")
        encrypted_tampered = encrypted[:-4] + bytes([encrypted[-4] ^ 0xFF]) + encrypted[-3:]
        
        try:
            crypto.decrypt(encrypted_tampered)
            print("✗ Should detect tampering")
            return False
        except ValueError as e:
            if "HMAC" in str(e):
                print("✓ Tampering detected")
            else:
                print(f"✗ Wrong error: {e}")
                return False
        
        # Test 5: Test replay detection
        print("[5] Test replay detection...")
        encrypted_new = crypto.encrypt(b'Another test!!!')
        
        # Try decrypt old frame again
        try:
            crypto.decrypt(encrypted)
            print("✗ Should detect replay")
            return False
        except ValueError as e:
            if "Replay" in str(e):
                print("✓ Replay detected")
            else:
                print(f"✗ Wrong error: {e}")
                return False
        
        print("\n=== ALL TESTS PASSED ===\n")
        return True


# ============================================================================
# USAGE WITH FSK CLASS
# ============================================================================

"""
Użycie z klasą FSK:

    from crypto_layer import CryptoLayer
    from FSK import FSK
    
    # Init FSK
    fsk = FSK(
        spiport=0,
        channel=0,
        interrupt=4,
        interrupt1=17,
        interrupt2=27,
        freq=868,
        tx_power=20,
        crypto=CryptoLayer(key)  # <-- pass crypto instance
    )
    
    # Wysyłanie
    fsk.write(plaintext)  # FSK automatycznie szyfruje
    
    # Odbieranie
    plaintext = fsk.read()  # FSK automatycznie deszyfruje

Istniejące metody FSK._encrypt/_decrypt będą działać transparentnie!
"""


if __name__ == "__main__":
    print("=== CRYPTO LAYER TEST ===\n")
    
    key = bytes.fromhex("AE6852F8121067CC4BF7A5765577F39E")
    crypto = CryptoLayer(key)
    
    # Simple test
    plaintext = b'Hello World!!!!\x00'
    encrypted = crypto.encrypt(plaintext)
    decrypted = crypto.decrypt(encrypted)
    
    print(f"\nOriginal:   {plaintext}")
    print(f"Encrypted:  {len(encrypted)} bytes")
    print(f"Decrypted:  {decrypted}")
    print(f"Match: {plaintext == decrypted}\n")
    
    # Self-test
    crypto.self_test()
