"""
kvas/handler.py - routes K-VAS `DataTransfer` messages (vendorId "kr.or.keco") to the
key server / decoder, and packages the result for both the OCPP response and the GUI.

D7 (the plan): GetEncKey must be answered SYNCHRONOUSLY inside the ocpp handler - the
return value of handle() *is* the DataTransferResponse.data - whereas the GUI update
is fire-and-forget, delivered as the second element of the tuple this returns.

CASING: `python-ocpp` recursively camelCase<->snake_case-converts every key of
`DataTransfer.data`/`DataTransferResponse.data`, same as it does for every named OCPP
field, because it treats `data` as untyped `Any` and applies the same generic
transform regardless (see `ocpp.charge_point.camel_to_snake_case()` /
`snake_to_camel_case()`). So the `data` dict this module RECEIVES already has
snake_case keys (`chargerKeySet` arrives as `charger_key_set`), and the dict it
RETURNS must also be snake_case for the library to camelCase it correctly on the way
out - build it in the KECO wire's camelCase directly and it goes out broken (verified
against the library's actual functions, and against `kvas_fake_charger.py`, before
writing this - see `keyserver.issue_key()`'s docstring for the concrete example)."""

import base64
import logging

from . import crypto
from . import record as record_decoder
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


def handle(vendor_id: str, message_id: str, data) -> tuple:
    """Returns (response_data, gui_event). response_data goes straight into
    DataTransferResponse.data (as an OBJECT - see TODO.md's data="" note for why that
    matters). gui_event is a dict for the GUI callback, or None if nothing to show."""
    if vendor_id != VENDOR_ID:
        return {}, None

    if message_id == MSG_GET_ENC_KEY:
        return _handle_get_enc_key(data)
    if message_id == MSG_BATTERY_INFO:
        return _handle_battery_info(data)

    logger.warning(f"kr.or.keco DataTransfer with unknown messageId '{message_id}': {data}")
    return {}, None


def _handle_get_enc_key(data: dict) -> tuple:
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


def _handle_battery_info(data: dict) -> tuple:
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
