"""
kvas/keyserver.py - answers the MCU's `GetEncKey` DataTransfer.

Plays the "ministry" role in the GetEncKey handshake (D2.1 in the plan: this is a role
in the MCU<->CSMS leg, NOT the CSMS<->KECO relay, which stays out of scope). Mints an
ephemeral P-256 keypair per request, signs it with the bench ministry key so the MCU's
`KvasCrypto_VerifyServerSignature()` accepts it, runs ECDHE against the charger's
public key, and derives+stores the same AES/HMAC session key the MCU derives - so this
process can decrypt every record the MCU encrypts.

D4 (deferred to Stage 3): always mints a fresh key and answers retVal "1" (new). The
"unchanged"/"renewed" lifecycle (retVal 2/3, returning the SAME ephemeral pubkey so the
MCU re-derives an identical key) is lifecycle emulation, not needed to get data
flowing.
"""

import json
import logging
import base64
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from . import crypto

logger = logging.getLogger("kvas.keyserver")

_KVAS_DIR = Path(__file__).resolve().parent
_MINISTRY_KEY_PATH = _KVAS_DIR / "bench_ministry.key"
_CHARGERS_DIR = _KVAS_DIR / "chargers"
_KEYS_STORE_PATH = _KVAS_DIR / "kvas_keys.json"

KEY_VALIDITY_DAYS = 30


class UnknownChargerError(Exception):
    pass


class KeyServer:
    """Holds every session key this CSMS has ever minted, keyed by keyId, so a
    Battery Info upload can be decrypted regardless of which key it was encrypted
    with (the MCU can hold an old key for up to 30 days). Persisted to
    kvas_keys.json so a CSMS restart does not orphan the key the MCU still holds."""

    def __init__(self):
        self._ministry_priv = self._load_ministry_key()
        self._keys = self._load_store()

    @staticmethod
    def _load_ministry_key():
        pem = _MINISTRY_KEY_PATH.read_bytes()
        return serialization.load_pem_private_key(pem, password=None)

    def _load_store(self) -> dict:
        if not _KEYS_STORE_PATH.exists():
            return {}
        try:
            with open(_KEYS_STORE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"kvas_keys.json unreadable ({e}), starting with an empty key store")
            return {}

    def _save_store(self):
        try:
            with open(_KEYS_STORE_PATH, "w") as f:
                json.dump(self._keys, f, indent=2)
        except OSError as e:
            logger.error(f"could not persist kvas_keys.json: {e}")

    def _charger_pubkey_path(self, charger_id: str) -> Path:
        return _CHARGERS_DIR / f"{charger_id}.pem"

    def has_charger(self, charger_id: str) -> bool:
        return self._charger_pubkey_path(charger_id).exists()

    def issue_key(self, charger_id: str) -> dict:
        """Mints a fresh session key for charger_id. Raises UnknownChargerError if we
        have no public key on file for it. Returns the fields needed to answer
        GetEncKey, keyed in snake_case (charger_id, key_id, encrypt_pub, sign_data,
        valid_time, ret_val) - NOT the KECO wire names.

        `python-ocpp` recursively converts `DataTransferResponse.data` from
        snake_case to camelCase before it hits the wire (it treats `data` as an
        untyped `Any` field and runs the same generic key transform it uses for every
        named field - see `ocpp.charge_point.snake_to_camel_case()`). Handing it
        `key_id` here is what makes the byte on the wire say `keyId`; handing it
        `keyId` directly would NOT be re-camelCased (no underscores to act on) and
        would happen to look right here, while `retVal` would NOT survive the
        matching camel_to_snake_case() on the way IN for the next request that
        includes it - verified against the library's actual functions (and against
        `kvas_fake_charger.py`) before writing this, not assumed."""
        pubkey_path = self._charger_pubkey_path(charger_id)
        if not pubkey_path.exists():
            raise UnknownChargerError(charger_id)

        charger_pub = crypto.load_public_key_pem(pubkey_path.read_bytes())

        eph_priv, eph_pub_der = crypto.generate_ephemeral_keypair()
        key_id = _make_key_id()

        sign_data = crypto.sign_key_id_and_pubkey(self._ministry_priv, key_id, eph_pub_der)

        z = crypto.ecdh_shared_secret(eph_priv, charger_pub)
        aes_key, mac_key = crypto.derive_session_keys(z)

        issued = datetime.utcnow()
        valid_time = issued + timedelta(days=KEY_VALIDITY_DAYS)
        valid_time_str = valid_time.strftime("%Y%m%d%H%M")

        self._keys[key_id] = {
            "chargerId": charger_id,
            "aesKey": aes_key.hex(),
            "macKey": mac_key.hex(),
            "issued": issued.isoformat() + "Z",
            "validTime": valid_time_str,
        }
        self._save_store()

        logger.info(f"issued session key {key_id} for charger {charger_id}, valid until {valid_time_str}")

        return {
            "charger_id": charger_id,
            "key_id": key_id,
            "encrypt_pub": base64.b64encode(eph_pub_der).decode("ascii"),
            "sign_data": base64.b64encode(sign_data).decode("ascii"),
            "valid_time": valid_time_str,
            "ret_val": "1",  # always "new" - see D4 above
        }

    def get_key(self, key_id: str):
        """Returns {"aesKey": bytes, "macKey": bytes, "chargerId": ...} or None."""
        entry = self._keys.get(key_id)
        if entry is None:
            return None
        return {
            "chargerId": entry["chargerId"],
            "aesKey": bytes.fromhex(entry["aesKey"]),
            "macKey": bytes.fromhex(entry["macKey"]),
            "validTime": entry["validTime"],
        }


def _make_key_id() -> str:
    """16 ASCII digits (KVAS_KEY_ID_LEN). KECO's samples look like
    YYMMDDhhmm + 6 more digits; we don't know their exact construction (it is not
    documented - TODO.md M10/O12), so this mirrors the *shape*, not a specified
    algorithm. Uniqueness matters more than format: pad with random digits, not a
    counter, so restarting the process cannot collide with a key an MCU still holds."""
    now = time.strftime("%y%m%d%H%M")
    suffix = f"{secrets.randbelow(10**6):06d}"
    return now + suffix
