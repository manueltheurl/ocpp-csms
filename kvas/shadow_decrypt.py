"""
kvas/shadow_decrypt.py - D8 (the plan): an OPTIONAL, development-only "shadow
decrypt" of Battery Info for the GUI while KVAS_MODE=relay, strictly gated to
charger identities whose private key is known-public KECO test material.

Why this is possible at all, and why it stops being possible for any real charger
(read this before touching the gate below): the `GetEncKey` handshake's security
rests on the CHARGER's own private key never being available to a third party (see
SmartyPluggerIotBoard's `.claude/docs/kvas-session-key-and-decryption.md` §4-5). For
KECO's test charger `ME12345601`, that private key isn't secret - it's KECO's openly
published test material, already sitting in this repo twice over
(`KVAS_Docs/Keys/.../cn=ME12345601,....key.pem`, and hardcoded into
`tools/kvas_fake_charger.py`). So while relaying real ciphertext for THAT charger
identity to the real KECO test server, this CSMS can additionally run the exact same
ECDH+KDF math KECO runs - a second, purely local, passive side-computation - and
decode the record for the GUI, without touching KECO's response, without the relay
being anything but a genuine, untouched forward of the real record.

THE KILL SWITCH (plan §7, stated here because this is the module that must honour
it): this module reads ONLY `test_only_charger_privkeys/`, which must ONLY EVER
contain KECO's own published test material. The day a real, KECO-provisioned
charger is pointed at this CSMS in relay mode, its private key must never land in
that directory - and in practice it never can, because a real charger's private key
does not exist anywhere off that charger's own hardware in the first place. The GUI
going blank for that charger's sessions is CORRECT behaviour, not a bug: it is not a
configuration gap, it is the cryptographic fact this whole module is built around.

Session keys derived here live IN MEMORY ONLY - never in kvas_keys.json (that
store is Stage-0-specific local-ministry state) - so a CSMS restart can't make
relay-mode "decoding" look like it's still working after the test-key file has
been removed, and so this key material is never at rest anywhere local_ministry
mode's persistence code touches.
"""

import logging
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from . import crypto

logger = logging.getLogger("kvas.shadow_decrypt")

_KVAS_DIR = Path(__file__).resolve().parent
TEST_PRIVKEYS_DIR = _KVAS_DIR / "test_only_charger_privkeys"

# keyId -> {"aesKey": bytes, "macKey": bytes, "chargerId": str}. In-memory only, by
# design - see the module docstring.
_shadow_keys = {}
_warned_chargers = set()  # so the loud activation log line only fires once/charger


def _privkey_path(charger_id: str) -> Path:
    return TEST_PRIVKEYS_DIR / f"{charger_id}.key.pem"


def is_test_charger(charger_id: str) -> bool:
    """True only if charger_id has a file on disk under test_only_charger_privkeys/
    - i.e. someone has explicitly placed KECO test material there. Never true for
    an arbitrary/unknown chargerId."""
    return _privkey_path(charger_id).exists()


def derive_and_store(charger_id: str, key_id: str, encrypt_pub_der: bytes) -> bool:
    """Call after relaying KECO's real GetEncKey response for charger_id, with the
    keyId KECO minted and A_pub (KECO's ephemeral pubkey, DER SPKI, observed while
    relaying it - not intercepted from anything secret). If charger_id is test
    material, derives the session key locally (same ECDH+KDF as local-ministry
    mode) and stores it in memory, returning True. No-op (returns False) for every
    charger_id that isn't published KECO test material - this is the gate."""
    path = _privkey_path(charger_id)
    if not path.exists():
        return False

    if charger_id not in _warned_chargers:
        logger.warning(
            f"shadow-decrypting chargerId={charger_id} using published TEST key "
            f"material - this will NOT work for any charger whose private key "
            f"isn't public")
        _warned_chargers.add(charger_id)

    try:
        charger_priv = serialization.load_pem_private_key(path.read_bytes(), password=None)
        eph_pub = crypto.load_public_key_der(encrypt_pub_der)
        z = crypto.ecdh_shared_secret(charger_priv, eph_pub)
        aes_key, mac_key = crypto.derive_session_keys(z)
    except Exception as e:
        logger.error(f"shadow-decrypt key derivation failed for {charger_id}/{key_id}: {e}")
        return False

    _shadow_keys[key_id] = {"aesKey": aes_key, "macKey": mac_key, "chargerId": charger_id}
    logger.info(f"shadow session key derived for keyId={key_id} (chargerId={charger_id})")
    return True


def get_key(key_id: str):
    """Returns {"aesKey": bytes, "macKey": bytes, "chargerId": ...} or None."""
    return _shadow_keys.get(key_id)
