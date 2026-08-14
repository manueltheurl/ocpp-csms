"""
kvas/config.py - env-driven config for K-VAS (plan D2/§3.3).

`KVAS_MODE` picks which role this CSMS plays in the `GetEncKey` handshake for the
whole process lifetime (D1: relay and local-ministry are alternatives selected per
mode, not stackable per message - see kvas/handler.py):

  - "local_ministry" (default): unchanged Stage 0 behaviour. This CSMS signs
    `GetEncKey` itself with the bench keypair and can decrypt every `Battery Info`
    upload for the GUI. No connection to KECO.
  - "relay": `GetEncKey`/`Battery Info` are forwarded to KECO's real REST API
    (kvas/relay.py) and answered with KECO's real response. This CSMS never sees a
    session key, so it can't decode `Battery Info` for the GUI in this mode - except
    for the narrow, explicitly-gated D8 shadow-decrypt case (kvas/shadow_decrypt.py).

The KECO host/port/bid/bkey default to the published TEST values
(`KVAS_Docs/downloadJfile3.pdf`) so `relay` mode works out of the box against the
test server with zero configuration. Production swaps all four via environment
variables - `bid`/`bkey` are institution credentials and must NEVER be committed
(D2), so there is deliberately no committed file for them, only this env-var
surface plus `.env.example` documenting the names.
"""

from environs import Env

env = Env()
env.read_env()  # loads .env if present; no-op (and no error) if it isn't

KVAS_MODE = env.str("KVAS_MODE", default="local_ministry")  # or "relay"

KVAS_KECO_HOST = env.str("KVAS_KECO_HOST", default="121.141.6.27")
KVAS_KECO_PORT = env.int("KVAS_KECO_PORT", default=35083)
KVAS_KECO_BID = env.str("KVAS_KECO_BID", default="JA")
KVAS_KECO_BKEY = env.str("KVAS_KECO_BKEY", default="JA_TEST")

_VALID_MODES = ("local_ministry", "relay")
if KVAS_MODE not in _VALID_MODES:
    raise ValueError(f"KVAS_MODE={KVAS_MODE!r} is not one of {_VALID_MODES}")
