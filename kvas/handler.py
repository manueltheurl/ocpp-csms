"""
kvas/handler.py - routes K-VAS `DataTransfer` messages (vendorId "kr.or.keco") to the
key server / decoder (KVAS_MODE=local_ministry) or to KECO's real REST API
(KVAS_MODE=relay, kvas/config.py), and packages the result for both the OCPP
response and the GUI.

D1 (the plan): local_ministry and relay are alternatives selected once, per process,
via KVAS_MODE - not stackable per message. Whichever keypair signs the GetEncKey
response is the one the charger derives its session key against, so there is no way
to "relay but also decrypt" without a second, unauthorized copy of a key KECO never
gives us (see kvas/config.py's docstring and the plan's TL;DR). The one narrow,
explicitly-gated exception is D8's shadow-decrypt (kvas/shadow_decrypt.py), which
recomputes the *same* math a second time locally using KECO's own published test
charger key - never a real charger's key.

D7 (the plan): GetEncKey must be answered SYNCHRONOUSLY inside the ocpp handler - the
return value of handle() *is* the DataTransferResponse.data - whereas the GUI update
is fire-and-forget, delivered as the second element of the tuple this returns.
`handle()` is `async def` (unlike Stage 0) so relay mode can `await` a
loop.run_in_executor() HTTP round trip to KECO (kvas/relay.py, plan D3) without
blocking the asyncio event loop every other charger's traffic shares - see
central_systems/central_system_v201.py's on_data_transfer for the caller side.
local_ministry mode does no I/O either way, so it isn't affected by handle() being
async - it just doesn't happen to await anything.

CASING: `python-ocpp` recursively camelCase<->snake_case-converts every key of
`DataTransfer.data`/`DataTransferResponse.data`, same as it does for every named OCPP
field, because it treats `data` as untyped `Any` and applies the same generic
transform regardless (see `ocpp.charge_point.camel_to_snake_case()` /
`snake_to_camel_case()`). So the `data` dict this module RECEIVES already has
snake_case keys (`chargerKeySet` arrives as `charger_key_set`), and the dict it
RETURNS must also be snake_case for the library to camelCase it correctly on the way
out - build it in the KECO wire's camelCase directly and it goes out broken (verified
against the library's actual functions, and against `kvas_fake_charger.py`, before
writing this - see `keyserver.issue_key()`'s docstring for the concrete example).
`kvas/relay.py`, in contrast, talks to KECO directly (not through python-ocpp), so
its payloads ARE plain KECO camelCase - the relay-mode functions below are the seam
that translates between the two casings."""

import asyncio
import base64
import logging

from . import config
from . import crypto
from . import record as record_decoder
from . import relay
from . import shadow_decrypt
from .keyserver import KeyServer, UnknownChargerError

logger = logging.getLogger("kvas.handler")

VENDOR_ID = "kr.or.keco"
MSG_GET_ENC_KEY = "GetEncKey"
MSG_BATTERY_INFO = "Battery Info"

_key_server = None


def _get_key_server() -> KeyServer:
    global _key_server
    if _key_server is None:
        _key_server = KeyServer()
    return _key_server


async def handle(vendor_id: str, message_id: str, data) -> tuple:
    """Returns (response_data, gui_event). response_data goes straight into
    DataTransferResponse.data (as an OBJECT - see TODO.md's data="" note for why that
    matters). gui_event is a dict for the GUI callback, or None if nothing to show."""
    if vendor_id != VENDOR_ID:
        return {}, None

    if message_id == MSG_GET_ENC_KEY:
        return await _handle_get_enc_key(data)
    if message_id == MSG_BATTERY_INFO:
        return await _handle_battery_info(data)

    logger.warning(f"kr.or.keco DataTransfer with unknown messageId '{message_id}': {data}")
    return {}, None


async def _handle_get_enc_key(data: dict) -> tuple:
    if config.KVAS_MODE == "relay":
        return await _handle_get_enc_key_relay(data)
    return _handle_get_enc_key_local(data)


async def _handle_battery_info(data: dict) -> tuple:
    if config.KVAS_MODE == "relay":
        return await _handle_battery_info_relay(data)
    return _handle_battery_info_local(data)


# ---------------------------------------------------------------------------
# local_ministry mode (Stage 0, unchanged)
# ---------------------------------------------------------------------------

def _handle_get_enc_key_local(data: dict) -> tuple:
    ks = _get_key_server()
    # Incoming: already snake_cased by python-ocpp (chargerKeySet -> charger_key_set).
    charger_set = (data or {}).get("charger_key_set", [])

    out_set = []
    for entry in charger_set:
        charger_id = entry.get("charger_id", "")
        try:
            issued = ks.issue_key(charger_id)
            out_set.append(issued)
            logger.info(f"GetEncKey: issued key {issued['key_id']} for {charger_id}")
        except UnknownChargerError:
            logger.error(f"GetEncKey: unknown chargerId '{charger_id}' - no public "
                         f"key on file in kvas/chargers/")
            out_set.append({
                "charger_id": charger_id,
                "key_id": "",
                "encrypt_pub": "",
                "sign_data": "",
                "valid_time": "",
                "ret_val": "9",
            })

    # Outgoing: snake_case in, camelCase (chargerCnt/chargerKeySet/...) out on the wire.
    response = {"charger_cnt": len(out_set), "charger_key_set": out_set}
    gui_event = {
        "type": "key_issued",
        "entries": [{"chargerId": e["charger_id"], "keyId": e["key_id"], "retVal": e["ret_val"]}
                    for e in out_set],
    }
    return response, gui_event


def _handle_battery_info_local(data: dict) -> tuple:
    ks = _get_key_server()
    data = data or {}
    # Incoming: already snake_cased (batteryDataSet -> battery_data_set, etc; sid/cid/
    # tsdt have no capitals so they pass through unchanged).
    battery_set = data.get("battery_data_set", [])
    sid = data.get("sid", "")
    cid = data.get("cid", "")
    tsdt = data.get("tsdt", "")
    envelope_key_id = data.get("key_id", "")

    decoded_records = []
    any_undecryptable = False

    for entry in battery_set:
        rec = {
            "timeStamp": entry.get("time_stamp"),
            "sessionDuration": entry.get("session_duration"),
            "counter": entry.get("counter"),
        }
        try:
            raw = base64.b64decode(entry.get("battery_data", ""))
        except Exception as e:
            rec["undecryptable"] = True
            rec["error"] = f"bad base64: {e}"
            decoded_records.append(rec)
            any_undecryptable = True
            continue

        if len(raw) < 32:
            rec["undecryptable"] = True
            rec["error"] = "record shorter than IV+keyId"
            decoded_records.append(rec)
            any_undecryptable = True
            continue

        # Look up the key by the keyId INSIDE the record, not the envelope one - a
        # batch can, in principle, span a key renewal (plan §0.4).
        record_key_id = raw[16:32].decode("ascii", errors="replace")
        key = ks.get_key(record_key_id)
        if key is None:
            rec["undecryptable"] = True
            rec["keyId"] = record_key_id
            rec["error"] = f"unknown keyId '{record_key_id}' - CSMS restarted since it was issued?"
            decoded_records.append(rec)
            any_undecryptable = True
            continue

        try:
            content, hmac_ok = crypto.decrypt_record(key["aesKey"], key["macKey"], raw)
        except ValueError as e:
            rec["undecryptable"] = True
            rec["keyId"] = record_key_id
            rec["error"] = f"decrypt failed: {e}"
            decoded_records.append(rec)
            any_undecryptable = True
            continue

        rec["keyId"] = record_key_id
        rec["hmac_ok"] = hmac_ok
        rec.update(record_decoder.decode(content))
        decoded_records.append(rec)

    if any_undecryptable:
        logger.warning(f"Battery Info from sid={sid}/cid={cid}: "
                        f"{sum(1 for r in decoded_records if r.get('undecryptable'))} of "
                        f"{len(decoded_records)} record(s) undecryptable")

    # Always resultCode "0": the MCU has no mid-session "re-request the key" recovery
    # path, so a rejection here just strands it (plan §3.4). Decryption failures are
    # logged above and surfaced to the GUI via gui_event instead of via resultCode.
    # snake_case in (result_code), camelCase (resultCode) out on the wire - see the
    # module docstring.
    response = {"result_code": "0"}
    gui_event = {
        "type": "battery_info",
        "sid": sid,
        "cid": cid,
        "tsdt": tsdt,
        "envelopeKeyId": envelope_key_id,
        "records": decoded_records,
    }
    return response, gui_event


# ---------------------------------------------------------------------------
# relay mode (this plan)
# ---------------------------------------------------------------------------

def _relay_error_entries(charger_set: list) -> list:
    """Shared fallback shape for a transport failure - matches local mode's
    "unknown charger" retVal 9 shape so the MCU's parsing path stays uniform
    regardless of *why* no real key came back."""
    return [{
        "charger_id": entry.get("charger_id", ""),
        "key_id": "",
        "encrypt_pub": "",
        "sign_data": "",
        "valid_time": "",
        "ret_val": "9",
    } for entry in charger_set]


async def _handle_get_enc_key_relay(data: dict) -> tuple:
    charger_set = (data or {}).get("charger_key_set", [])

    # snake_case in -> KECO's own camelCase out (relay.py talks to KECO directly,
    # not through python-ocpp - see the module docstring's CASING note). keyId is
    # omitted on a first request, present on renewal (kvas-explained.md §6).
    wire_set = []
    for entry in charger_set:
        w = {"chargerId": entry.get("charger_id", "")}
        if entry.get("key_id"):
            w["keyId"] = entry["key_id"]
        wire_set.append(w)

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, relay.get_enc_key, wire_set)
    except relay.KecoRelayError as e:
        logger.error(f"GetEncKey relay to KECO failed: {e}")
        out_set = _relay_error_entries(charger_set)
        response = {"charger_cnt": len(out_set), "charger_key_set": out_set}
        gui_event = {
            "type": "key_issued",
            "entries": [{"chargerId": e["charger_id"], "keyId": "", "retVal": "9"} for e in out_set],
            "relayError": str(e),
        }
        return response, gui_event

    out_set = []
    for entry in result.get("chargerKeySet", []):
        charger_id = entry.get("chargerId", "")
        key_id = entry.get("keyId", "")
        ret_val = entry.get("retVal", "9")
        out_set.append({
            "charger_id": charger_id,
            "key_id": key_id,
            "encrypt_pub": entry.get("encryptPub", ""),
            "sign_data": entry.get("signData", ""),
            "valid_time": entry.get("validTime", ""),
            "ret_val": ret_val,
        })
        logger.info(f"GetEncKey (relay): KECO issued key {key_id} for {charger_id} "
                    f"(retVal={ret_val})")

        # D8: purely additional, gated, in-memory-only side computation for the GUI.
        # Never touches, delays, or depends on the response already built above -
        # what goes back to the MCU is KECO's real answer either way.
        if key_id and entry.get("encryptPub"):
            try:
                eph_pub_der = base64.b64decode(entry["encryptPub"])
                shadow_decrypt.derive_and_store(charger_id, key_id, eph_pub_der)
            except Exception as e:
                logger.warning(f"shadow-decrypt GetEncKey side-computation skipped "
                                f"for {charger_id}: {e}")

    response = {"charger_cnt": len(out_set), "charger_key_set": out_set}
    gui_event = {
        "type": "key_issued",
        "entries": [{"chargerId": e["charger_id"], "keyId": e["key_id"], "retVal": e["ret_val"]}
                    for e in out_set],
        "resultCode": result.get("resultCode"),
        "errCode": result.get("errCode"),
        "errMsg": result.get("errMsg"),
    }
    return response, gui_event


def _shadow_decode_records(envelope_key_id: str, battery_set: list):
    """D8: best-effort, GUI-only decode of the just-relayed records, gated to
    charger identities with a known-public test private key (kvas/shadow_decrypt.py
    is the actual gate - this function just calls it per record, same as
    _handle_battery_info_local's decrypt loop). Returns None if no shadow key is on
    file for this batch's keyId at all (the common case - shadow decrypt is off by
    default), otherwise a list shaped like local mode's `records` (never raises -
    any failure shows up as `undecryptable` on that one record, exactly like local
    mode)."""
    if shadow_decrypt.get_key(envelope_key_id) is None:
        return None

    out = []
    for entry in battery_set:
        rec = {
            "timeStamp": entry.get("time_stamp"),
            "sessionDuration": entry.get("session_duration"),
            "counter": entry.get("counter"),
        }
        try:
            raw = base64.b64decode(entry.get("battery_data", ""))
            if len(raw) < 32:
                raise ValueError("record shorter than IV+keyId")
            record_key_id = raw[16:32].decode("ascii", errors="replace")
            key = shadow_decrypt.get_key(record_key_id)
            if key is None:
                raise ValueError(f"no shadow key for keyId '{record_key_id}'")
            content, hmac_ok = crypto.decrypt_record(key["aesKey"], key["macKey"], raw)
            rec["keyId"] = record_key_id
            rec["hmac_ok"] = hmac_ok
            rec.update(record_decoder.decode(content))
        except Exception as e:
            rec["undecryptable"] = True
            rec["error"] = f"shadow-decrypt: {e}"
        out.append(rec)
    return out


async def _handle_battery_info_relay(data: dict) -> tuple:
    data = data or {}
    battery_set = data.get("battery_data_set", [])
    sid = data.get("sid", "")
    cid = data.get("cid", "")
    tsdt = data.get("tsdt", "")
    envelope_key_id = data.get("key_id", "")

    # D5: near-verbatim forward - same fields KECO's rcvData wants, camelCased back
    # out, ciphertext untouched. No AES/HMAC/TLV walk on this path at all.
    wire_set = []
    for entry in battery_set:
        w = {"counter": entry.get("counter"), "batteryData": entry.get("battery_data", "")}
        if entry.get("time_stamp") is not None:
            w["timeStamp"] = entry["time_stamp"]
        if entry.get("session_duration") is not None:
            w["sessionDuration"] = entry["session_duration"]
        wire_set.append(w)

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, relay.send_battery_data, sid, cid, tsdt, envelope_key_id, wire_set)
    except relay.KecoRelayError as e:
        logger.error(f"Battery Info relay to KECO failed: {e}")
        # "2" = KECO's own "failure" resultCode (plan §4.2) - we genuinely don't
        # know if KECO got this, so answering anything but a real success is
        # correct; the MCU's 60s/3-strike retry logic on ITS side is the right
        # reaction here (D6).
        response = {"result_code": "2"}
        gui_event = {
            "type": "battery_relayed",
            "sid": sid, "cid": cid, "tsdt": tsdt, "keyId": envelope_key_id,
            "recordCount": len(battery_set),
            "resultCode": "2", "resultTime": None, "successCnt": 0,
            "errCode": None, "errMsg": str(e),
            "shadowDecoded": None,
        }
        return response, gui_event

    shadow_records = None
    try:
        shadow_records = _shadow_decode_records(envelope_key_id, battery_set)
    except Exception as e:
        # Belt and braces: _shadow_decode_records already swallows per-record
        # errors, but this must never be able to affect the relay result above.
        logger.error(f"shadow-decrypt side-computation raised unexpectedly: {e}")

    result_code = str(result.get("resultCode", "2"))
    if result_code != "0":
        logger.warning(f"Battery Info (relay) from sid={sid}/cid={cid}: KECO resultCode="
                        f"{result_code} errCode={result.get('errCode')} errMsg={result.get('errMsg')}")

    # D6 (confirmed §8): relay KECO's REAL resultCode, never an unconditional "0" -
    # unlike local_ministry mode's Stage 0 accommodation (see that function's
    # comment above) - masking a real KECO failure would defeat the entire point of
    # this mode.
    response = {"result_code": result_code}
    gui_event = {
        "type": "battery_relayed",
        "sid": sid, "cid": cid, "tsdt": tsdt, "keyId": envelope_key_id,
        "recordCount": len(battery_set),
        "resultCode": result.get("resultCode"),
        "resultTime": result.get("resultTime"),
        "successCnt": result.get("successCnt"),
        "errCode": result.get("errCode"),
        "errMsg": result.get("errMsg"),
        "shadowDecoded": shadow_records,
    }
    return response, gui_event
