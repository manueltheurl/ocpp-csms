# K-VAS support (`ocpp-csms/kvas/`)

Implements the CSMS half of
[SmartyPluggerIotBoard's K-VAS plan](../../SmartyPluggerIotBoard/.claude/plans/2026-08-11-kvas-battery-data-to-csms.md)
(Stage 0). Read that plan first — this file is just the map of what lives where.

**D1/D2 (the plan)**: this CSMS plays the "ministry" role in the MCU's `GetEncKey`
handshake *locally*, using a **bench** keypair — never KECO's real `ME_Server` key,
which we do not have. That is what lets it derive the same session key the MCU
derives and decrypt every `Battery Info` upload. It is **not** the CSMS↔KECO relay
(that stays out of scope — see the plan §7 and `TODO.md` "Relay mode B").

| File | Role |
|---|---|
| `crypto.py` | KDF, TLS padding, AES-CBC+HMAC record encrypt/decrypt, ECDHE, ECDSA-sign — ported from the MCU repo's `_App/Kvas/tools/kvas_reference.py`, which is itself a faithful port of KECO's own Java/C++ reference code. |
| `keyserver.py` | Answers `GetEncKey`: mints an ephemeral P-256 keypair per request, signs it with `bench_ministry.key`, runs ECDHE against the charger's public key (`chargers/<chargerId>.pem`), derives the session key, and persists it to `kvas_keys.json` (gitignored — runtime state, regenerated on first request after a restart). |
| `record.py` | Decodes the K-VAS TLV plaintext (tag table in `.claude/docs/kvas-vas-record-format.md` in the MCU repo) into scaled physical values. Never raises — a malformed record comes back as `{"parse_error": ..., "raw_hex": ...}`. |
| `handler.py` | Routes `vendorId == "kr.or.keco"` `DataTransfer` messages to the above and returns `(response_data, gui_event)` — see `central_system_v201.py`'s `on_data_transfer`. |
| `bench_ministry.key` / `.pem` | The bench keypair (P-256, self-signed, `CN=BENCH_ME_Server`, 10y validity). The **public** half (`.pem`, as a certificate) is what gets compiled into the MCU's `KvasCredentials.h` behind `KVAS_TEST_MODE_LOCAL_MINISTRY`. Minted with the two `openssl` commands in the plan's §D2. |
| `chargers/ME12345601.pem` | The **public** half of the KECO-test charger private key baked into the MCU's `KVAS_CHARGER_PRIVATE_KEY_PEM` (`KvasCredentials.h`), extracted with `cryptography`. Used to compute the ECDH shared secret `Z`. Add one file per `chargerId` here as more chargers are provisioned; an unknown `chargerId` gets `retVal "9"`. |

## Why this isn't in `app.py`

`GetEncKey` must be answered **synchronously** — the return value of `on_data_transfer`
*is* the OCPP response — whereas `app.py`'s other DataTransfer callbacks are
fire-and-forget GUI updates. `handler.handle()` returns both: the synchronous response
for the OCPP layer, and a `gui_event` dict that `app.py` relays to the browser over
Socket.IO (see `on_kvas_key_issued` / `on_kvas_battery_record` there).

## Key material and resultCode

Every `Battery Info` upload gets `resultCode: "0"` unconditionally, even when a record
fails to decrypt. The MCU has no mid-session "re-request the key" recovery path — a
non-`"0"` `resultCode` just triggers its 60 s retry / 3-strike-abandon logic (see
`TODO.md`'s "CSMS returns `data=""`" note in the MCU repo for the concrete failure mode
that motivated always sending an *object*, never a string, in `data`). Decrypt/HMAC
failures are logged loudly here and shown in the GUI instead.
