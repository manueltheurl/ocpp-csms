"""
kvas/crypto.py - K-VAS battery-data cryptography, CSMS side.

Ported from SmartyPluggerIotBoard's `_App/Kvas/tools/kvas_reference.py`, which is
itself a faithful port of KECO's own reference code (Java `AesHmac.java`/`Ecdhe.java`,
C++ `key_exchange.cpp` - see that file's header for the full provenance). Copied
rather than imported across repos, per the plan's D7 - the two repos are independent
and this file only needs to change if KECO's own crypto changes.

Do NOT "fix" this to match `KVAS_Documents/K-VAS_zPlug/src/kvas_crypto.cpp` - that
package is AI-generated and wrong in four places (see the MCU repo's TODO.md, M1-M4).
"""

import hashlib
import hmac

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend

# ---------------------------------------------------------------------------
# KDF - NIST SP 800-56A concatenation KDF, exactly as Ecdhe.generateSessionKey()
# and both_kdf() do it.
#
#   input = counter(4, BE, =1) || Z || keydatalen(4, BE, =384) || AlgID || ID_U || ID_V
#   derived = SHA-384(input)            [48 bytes]
#   aesKey  = derived[0:16]             top 128 bits
#   macKey  = derived[16:48]            bottom 256 bits
# ---------------------------------------------------------------------------
KDF_ALG_ID = 0x01
KDF_ID_U = 0x55  # server  (KECO / us, playing the ministry)
KDF_ID_V = 0x56  # client  (EVSE)
KDF_KEYDATALEN_BITS = 384


def derive_session_keys(z: bytes):
    buf = (
        (1).to_bytes(4, "big")
        + z
        + KDF_KEYDATALEN_BITS.to_bytes(4, "big")
        + bytes([KDF_ALG_ID, KDF_ID_U, KDF_ID_V])
    )
    derived = hashlib.sha384(buf).digest()
    assert len(derived) == 48
    return derived[:16], derived[16:48]  # aesKey, macKey


# ---------------------------------------------------------------------------
# TLS-style padding (NOT PKCS#7): padLen = 15 - (len % 16); append padLen+1 bytes,
# each equal to padLen.
# ---------------------------------------------------------------------------
def tls_pad(data: bytes) -> bytes:
    pad_len = 15 - (len(data) % 16)
    return data + bytes([pad_len]) * (pad_len + 1)


def tls_unpad(data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("bad length")
    pad_len = data[-1]
    total = pad_len + 1
    if total > len(data):
        raise ValueError("pad longer than data")
    if any(b != pad_len for b in data[-total:]):
        raise ValueError("bad padding content")
    return data[:-total]


# ---------------------------------------------------------------------------
# GenericBlockCipher record, as AesHmac.encrypt()/decrypt().
#
#   record = IV(16) || keyId(16 ASCII) || AES-128-CBC-NoPadding(
#                                            content || HMAC-SHA256(content) || TLSpad )
#
# MAC-then-encrypt; the HMAC covers the PLAINTEXT CONTENT ONLY - not the IV, not the
# keyId.
# ---------------------------------------------------------------------------
def encrypt_record(aes_key: bytes, mac_key: bytes, key_id: bytes, content: bytes,
                    iv: bytes) -> bytes:
    assert len(iv) == 16 and len(aes_key) == 16 and len(mac_key) == 32
    mac = hmac.new(mac_key, content, hashlib.sha256).digest()
    padded = tls_pad(content + mac)
    enc = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return iv + key_id + ct


def decrypt_record(aes_key: bytes, mac_key: bytes, record: bytes,
                    key_id_len: int = 16):
    """Returns (content, hmac_ok). Raises ValueError on a structurally bad record
    (short, misaligned, bad padding) - a HMAC mismatch is reported via hmac_ok=False
    instead of raising, so a caller can still show the (untrusted) plaintext for
    debugging without treating a wrong key as a crash."""
    if len(record) < 16 + key_id_len + 16:
        raise ValueError("record too short")
    iv, key_id, ct = record[:16], record[16:16 + key_id_len], record[16 + key_id_len:]
    if len(ct) == 0 or len(ct) % 16:
        raise ValueError("ciphertext not block-aligned")
    dec = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    body = tls_unpad(padded)
    content, mac = body[:-32], body[-32:]
    hmac_ok = hmac.compare_digest(mac, hmac.new(mac_key, content, hashlib.sha256).digest())
    return content, hmac_ok


# ---------------------------------------------------------------------------
# ECDHE - P-256, matching KvasCrypto_ComputeSharedSecret() on the MCU: shared secret
# Z is the raw 32-byte X coordinate (cryptography's exchange() already returns just
# that for EC keys).
# ---------------------------------------------------------------------------
def generate_ephemeral_keypair():
    """Returns (private_key, public_key_der_spki_bytes)."""
    priv = ec.generate_private_key(ec.SECP256R1(), default_backend())
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, pub_der


def load_public_key_der(der_spki: bytes):
    return serialization.load_der_public_key(der_spki, default_backend())


def load_public_key_pem(pem_bytes: bytes):
    return serialization.load_pem_public_key(pem_bytes, default_backend())


def ecdh_shared_secret(priv, peer_pub) -> bytes:
    """Z = ECDHE(priv, peer_pub), raw X coordinate, 32 bytes for P-256."""
    z = priv.exchange(ec.ECDH(), peer_pub)
    assert len(z) == 32
    return z


def sign_key_id_and_pubkey(ministry_priv, key_id: str, pub_der_spki: bytes) -> bytes:
    """signData: ECDSA-SHA256(DER) over SHA-256(keyId_ascii(16) || DER_SPKI_bytes),
    matching KvasCrypto_VerifyServerSignature() on the MCU
    (see KvasCrypto.c:298-309 in the SmartyPluggerIotBoard repo). Note it is the
    *decoded* SPKI bytes that are signed, not the base64 text."""
    assert len(key_id) == 16
    message = key_id.encode("ascii") + pub_der_spki
    return ministry_priv.sign(message, ec.ECDSA(hashes.SHA256()))
