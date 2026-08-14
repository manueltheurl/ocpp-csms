# K-VAS support (`ocpp-csms/kvas/`)

Implements the CSMS half of
[SmartyPluggerIotBoard's K-VAS plan](../../SmartyPluggerIotBoard/.claude/plans/2026-08-11-kvas-battery-data-to-csms.md)
(Stage 0) **plus** the CSMS↔KECO relay from
[`.vscode/plans/2026-08-12-kvas-relay-to-keco.md`](../.vscode/plans/2026-08-12-kvas-relay-to-keco.md).
Read those plans first — this file is just the map of what lives where.

## Two mutually-exclusive modes (`KVAS_MODE`, D1/D2)

This CSMS plays the "ministry" role in the MCU's `GetEncKey` handshake either
**locally** or by **relaying to KECO's real server** — picked once, per process, by
the `KVAS_MODE` environment variable (`kvas/config.py`, defaults live in
`.env.example` at the repo root). The two modes are **not stackable**: whichever
keypair signs `GetEncKey` is the one the charger derives its session key against, so
there is no way to relay *and* decrypt for the GUI without a second, unauthorized
copy of a key KECO never gives us.

| | `local_ministry` (default) | `relay` |
|---|---|---|
| Signs `GetEncKey` with | our own **bench** keypair (`bench_ministry.key`) | KECO's real `ME_Server` key (forwarded, not held here) |
| `Battery Info` | decrypted locally, decoded for the GUI | forwarded near-verbatim to KECO; answered with KECO's **real** `resultCode` (D6) |
| GUI shows | decoded SoC/SoH/pack V/A/cell V/T etc. | relay status: KECO's `resultCode`/`successCnt`/error fields |
| Talks to KECO? | never | yes — `kvas/relay.py`, `KVAS_KECO_HOST`/`_PORT`/`_BID`/`_BKEY` |

**Exception, test-server-only (D8):** against KECO's **test** server, with the
bundled **`ME12345601`** test charger identity specifically, decoding is still
possible in `relay` mode too — because that one charger's private key is KECO's own
openly-published test material, not anything secret. See `kvas/shadow_decrypt.py`'s
docstring for the full explanation and its kill switch. This is a development
convenience, off by default, and stops being possible *by the math itself* — not a
setting — the moment a real charger is in the loop.

| File | Role |
|---|---|
| `config.py` | `KVAS_MODE` / `KVAS_KECO_HOST` / `_PORT` / `_BID` / `_BKEY`, all env-driven (`environs`), all defaulting to KECO's public test values. |
| `crypto.py` | KDF, TLS padding, AES-CBC+HMAC record encrypt/decrypt, ECDHE, ECDSA-sign — ported from the MCU repo's `_App/Kvas/tools/kvas_reference.py`, which is itself a faithful port of KECO's own Java/C++ reference code. Used by both modes (`local_ministry` to decrypt everything; `relay` only for the D8 shadow path). |
| `keyserver.py` | `local_ministry` only. Answers `GetEncKey`: mints an ephemeral P-256 keypair per request, signs it with `bench_ministry.key`, runs ECDHE against the charger's public key (`chargers/<chargerId>.pem`), derives the session key, and persists it to `kvas_keys.json` (gitignored — runtime state, regenerated on first request after a restart). |
| `relay.py` | `relay` only. Synchronous `requests` client for KECO's real `getEncKey`/`rcvData` REST endpoints — chunking, KECO's own 60s/3-strike retry contract (D7), and the manual exchange log (`keco_exchange.log`, gitignored, `bkey` always redacted). Called via `loop.run_in_executor()` from `handler.py` so a slow/hanging KECO round trip never blocks the asyncio event loop every other charger's OCPP traffic shares (D3). |
| `shadow_decrypt.py` | D8, `relay` mode only, off by default. Recomputes the session key locally for charger identities with a file under `test_only_charger_privkeys/` (gitignored; today just `ME12345601.key.pem`, KECO's own published test material) so the GUI can additionally show decoded values while relay mode is genuinely, separately, forwarding to KECO. Keys live in memory only, never on disk. |
| `record.py` | Decodes the K-VAS TLV plaintext (tag table in `.claude/docs/kvas-vas-record-format.md` in the MCU repo) into scaled physical values. Never raises — a malformed record comes back as `{"parse_error": ..., "raw_hex": ...}`. Used by `local_ministry` mode and by D8's shadow decrypt. |
| `handler.py` | Routes `vendorId == "kr.or.keco"` `DataTransfer` messages to the above (branching on `KVAS_MODE`) and returns `(response_data, gui_event)` — see `central_system_v201.py`'s `on_data_transfer`. `handle()` is `async def` so `relay` mode can `await` the `run_in_executor()` HTTP call (D3); `local_ministry` mode does no I/O and isn't affected by that. |
| `bench_ministry.key` / `.pem` | `local_ministry` only. The bench keypair (P-256, self-signed, `CN=BENCH_ME_Server`, 10y validity). The **public** half (`.pem`, as a certificate) is what gets compiled into the MCU's `KvasCredentials.h` behind `KVAS_TEST_MODE_LOCAL_MINISTRY`. Minted with the two `openssl` commands in the Stage 0 plan's §D2. |
| `chargers/ME12345601.pem` | `local_ministry` only. The **public** half of the KECO-test charger private key baked into the MCU's `KVAS_CHARGER_PRIVATE_KEY_PEM` (`KvasCredentials.h`), extracted with `cryptography`. Used to compute the ECDH shared secret `Z`. Add one file per `chargerId` here as more chargers are provisioned; an unknown `chargerId` gets `retVal "9"`. |
| `test_only_charger_privkeys/` | D8/`shadow_decrypt.py` only, gitignored. **Only** ever KECO's own published test charger PRIVATE keys — see that module's docstring for why a real charger's key can never end up here. |

## Trying `relay` mode

```
cp .env.example .env      # then edit KVAS_MODE=relay (test-server defaults need no other changes)
```
Restart the CSMS, then run the bring-up order from the relay plan's §6:
`tools/kvas_fake_charger.py --relay` drives the whole thing against KECO's real test
server with no board involved — watch the GUI's "EV Battery (K-VAS)" card switch to
relay-status display.

## Why this isn't in `app.py`

`GetEncKey` must be answered **synchronously** — the return value of `on_data_transfer`
*is* the OCPP response — whereas `app.py`'s other DataTransfer callbacks are
fire-and-forget GUI updates. `handler.handle()` returns both: the synchronous response
for the OCPP layer, and a `gui_event` dict that `app.py` relays to the browser over
Socket.IO (see `on_kvas_key_issued` / `on_kvas_battery_record` there).

## Key material and resultCode

**`local_ministry` mode:** every `Battery Info` upload gets `resultCode: "0"`
unconditionally, even when a record fails to decrypt. The MCU has no mid-session
"re-request the key" recovery path — a non-`"0"` `resultCode` just triggers its
60 s retry / 3-strike-abandon logic (see `TODO.md`'s "CSMS returns `data=""`" note in
the MCU repo for the concrete failure mode that motivated always sending an
*object*, never a string, in `data`). Decrypt/HMAC failures are logged loudly here
and shown in the GUI instead.

**`relay` mode (D6):** the opposite — `resultCode` is KECO's **real** answer,
forwarded as-is. Masking a genuine KECO rejection behind a fake `"0"` would defeat
the entire point of this mode (observing whether KECO actually accepts the data).
If a real MCU ever runs against `relay` mode, a non-`"0"` correctly triggers its
60 s retry / 3-strike-abandon logic — that's KECO saying no, not a CSMS bug.
