"""
kvas/relay.py - HTTP client for KECO's real REST API (CSMS -> KECO), used only when
KVAS_MODE=relay (kvas/config.py). Implements the wire contracts pinned in the plan's
§4: `/v1/charger/battery/getEncKey` and `/v1/charger/battery/rcvData`.

SYNCHRONOUS ON PURPOSE (plan D3): plain `requests`, not `aiohttp`. The whole CSMS runs
on a single asyncio event loop shared by every connected charger
(central_systems/central_system_v201.py, GUI/web/app.py's websocket server), so a
blocking call in here would stall every other charger's OCPP traffic for the duration
of the HTTP round trip (and, per D7 below, potentially for minutes if KECO is
unhappy). The caller (kvas/handler.py's relay-mode branch) MUST run these functions
via `loop.run_in_executor()`, never call them directly from an `async def`.

Never logs `bkey` (plan §1.2 item 8) - see `_redact()`.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config

logger = logging.getLogger("kvas.relay")

_GET_ENC_KEY_PATH = "/v1/charger/battery/getEncKey"
_RCV_DATA_PATH = "/v1/charger/battery/rcvData"

# Plan §1.2 item 5 / §4: batch limits KECO enforces server-side. We chunk
# defensively rather than assume a caller never exceeds them.
_MAX_CHARGERS_PER_GET_ENC_KEY = 100
_MAX_RECORDS_PER_RCV_DATA = 20

# D7: KECO's own retry contract on the CSMS->KECO leg, independent of whatever the
# MCU is doing. Non-"0" resultCode -> wait 60s, resend the identical request; 3
# identical failures -> stop and escalate (log + return the last result, don't raise
# - a KecoRelayError is reserved for transport failures, see _post()).
_RETRY_DELAY_S = 60
_RETRY_MAX_ATTEMPTS = 3

_TIMEOUT_S = 15

# 5.8: manual exchange log, `bkey` always redacted before it ever reaches this file.
_EXCHANGE_LOG_PATH = Path(__file__).resolve().parent / "keco_exchange.log"

# D7's resultCode ranking so a partial failure across chunks reduces to the
# worst-case resultCode rather than just "whichever chunk answered last".
_RESULT_CODE_SEVERITY = {"0": 0, "1": 1, "2": 2}


class KecoRelayError(Exception):
    """Raised on transport failure talking to KECO - timeout, connection refused,
    non-2xx, or a malformed/non-JSON body. A parsed KECO response with a non-"0"
    resultCode is NOT an error at this layer (that's D7's retry contract, handled
    internally) - this is reserved for "we don't know what KECO said", which the
    caller (kvas/handler.py) must decide how to answer the MCU for."""


def _redact(payload: dict) -> dict:
    """Never-log-bkey (plan §1.2 item 8) - copy with bkey masked for log lines."""
    red = dict(payload)
    if "bkey" in red:
        red["bkey"] = "***REDACTED***"
    return red


def _log_exchange(direction: str, path: str, payload: dict):
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "path": path,
        "payload": _redact(payload),
    }
    try:
        with open(_EXCHANGE_LOG_PATH, "a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError as e:
        logger.warning(f"could not write keco_exchange.log: {e}")


def _base_url() -> str:
    return f"http://{config.KVAS_KECO_HOST}:{config.KVAS_KECO_PORT}"


def _post(path: str, payload: dict) -> dict:
    """Single HTTP round trip. Raises KecoRelayError on any transport failure;
    returns KECO's parsed JSON body (whatever its resultCode) otherwise."""
    url = _base_url() + path
    _log_exchange("request", path, payload)
    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT_S,
                              headers={"Content-Type": "application/json"})
    except requests.RequestException as e:
        raise KecoRelayError(f"transport error POSTing {path}: {e}") from e

    if resp.status_code != 200:
        raise KecoRelayError(f"{path} returned HTTP {resp.status_code}: {resp.text[:500]!r}")

    try:
        parsed = resp.json()
    except ValueError as e:
        raise KecoRelayError(f"{path} returned non-JSON body: {e}") from e

    _log_exchange("response", path, parsed)
    return parsed


def _post_with_retry(path: str, payload: dict) -> dict:
    """D7: resend the identical request after 60s if resultCode != "0"; give up
    (log + return the last result) after 3 attempts. Transport failures
    (KecoRelayError) are NOT retried here - they propagate immediately, since D7 is
    specifically about KECO answering with a real, parsed, non-"0" resultCode."""
    result = None
    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        result = _post(path, payload)
        result_code = str(result.get("resultCode", ""))
        if result_code == "0":
            return result
        logger.warning(
            f"KECO {path} resultCode={result_code!r} (attempt {attempt}/{_RETRY_MAX_ATTEMPTS}) "
            f"errCode={result.get('errCode')} errMsg={result.get('errMsg')}")
        if attempt < _RETRY_MAX_ATTEMPTS:
            time.sleep(_RETRY_DELAY_S)
    logger.error(
        f"KECO {path}: {_RETRY_MAX_ATTEMPTS} non-zero resultCode responses in a row, "
        f"giving up - last result: {result}")
    return result


def _severity(result_code) -> int:
    return _RESULT_CODE_SEVERITY.get(str(result_code), 99)


def _merge_result(merged: dict, result: dict, list_key: str = None):
    """Folds one chunk's result into the running aggregate: successCnt sums, the
    worst resultCode/errCode/errMsg wins (plan §3.1: 'a partial failure across
    chunks reduces to worst-case resultCode'), list_key's list (if any) extends."""
    merged["successCnt"] += result.get("successCnt") or 0
    if result.get("resultTime"):
        merged["resultTime"] = result["resultTime"]
    if _severity(result.get("resultCode")) > _severity(merged.get("resultCode")):
        merged["resultCode"] = result.get("resultCode")
        merged["errCode"] = result.get("errCode")
        merged["errMsg"] = result.get("errMsg")
    if list_key and result.get(list_key):
        merged.setdefault(list_key, []).extend(result[list_key])


def get_enc_key(charger_key_set: list) -> dict:
    """POSTs {bid, bkey, chargerCnt, chargerKeySet} to .../getEncKey, chunked to
    <=100 chargers/call (§4/§1.2 item 5), applying D7's retry contract per chunk.
    Returns KECO's (possibly chunk-merged) parsed JSON response - resultCode,
    chargerKeySet[] (keyId/encryptPub/signData/validTime/retVal per entry),
    optionally errCode/errMsg. Raises KecoRelayError on transport failure."""
    if not charger_key_set:
        return {"resultCode": "0", "successCnt": 0, "chargerKeySet": []}

    chunks = [charger_key_set[i:i + _MAX_CHARGERS_PER_GET_ENC_KEY]
              for i in range(0, len(charger_key_set), _MAX_CHARGERS_PER_GET_ENC_KEY)]

    merged = {"resultCode": "0", "resultTime": None, "successCnt": 0, "chargerKeySet": []}
    for chunk in chunks:
        payload = {
            "bid": config.KVAS_KECO_BID,
            "bkey": config.KVAS_KECO_BKEY,
            "chargerCnt": len(chunk),
            "chargerKeySet": chunk,
        }
        result = _post_with_retry(_GET_ENC_KEY_PATH, payload)
        _merge_result(merged, result, list_key="chargerKeySet")
    return merged


def send_battery_data(sid: str, cid: str, tsdt: str, key_id: str,
                       battery_data_set: list) -> dict:
    """POSTs {bid, bkey, sid, cid, tsdt, keyId, infoCnt, batteryDataSet} to
    .../rcvData, chunked to <=20 records/call (§4/§1.2 item 5), applying D7's retry
    contract per chunk. Returns the aggregated result (resultCode/resultTime/
    successCnt/errCode/errMsg). Raises KecoRelayError on transport failure."""
    if not battery_data_set:
        return {"resultCode": "0", "successCnt": 0}

    chunks = [battery_data_set[i:i + _MAX_RECORDS_PER_RCV_DATA]
              for i in range(0, len(battery_data_set), _MAX_RECORDS_PER_RCV_DATA)]

    merged = {"resultCode": "0", "resultTime": None, "successCnt": 0}
    for chunk in chunks:
        payload = {
            "bid": config.KVAS_KECO_BID,
            "bkey": config.KVAS_KECO_BKEY,
            "sid": sid, "cid": cid, "tsdt": tsdt, "keyId": key_id,
            "infoCnt": len(chunk),
            "batteryDataSet": chunk,
        }
        result = _post_with_retry(_RCV_DATA_PATH, payload)
        _merge_result(merged, result)
    return merged
