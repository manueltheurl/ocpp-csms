#!/usr/bin/env python3
"""
kvas_fake_charger.py - exercises the entire CSMS K-VAS half with NO board and NO EV.

Connects to the running CSMS as an OCPP 2.0.1 charge point, does BootNotification,
sends GetEncKey, then encrypts and uploads the 9 real records from
SmartyPluggerIotBoard's `_App/Kvas/tools/vas_reference_records.txt` using the charger
private key from that repo's `KvasCredentials.h` - i.e. the exact key material the
real MCU would use. This is bring-up rung #2 in the plan (§5): "kvas_fake_charger.py
-> CSMS -> GUI. Whole CSMS half proven with no hardware."

Usage:
    python3 tools/kvas_fake_charger.py [--url ws://localhost:9000] [--cp-id TEST_CP_016]

Watch the GUI at http://localhost:1234 while this runs - the "EV Battery (K-VAS)"
card should populate with the 9 records' decoded values (SoC 50->50->...->90%,
matching the header comment in vas_reference_records.txt) and hmac_ok=True.

--relay: exercises KVAS_MODE=relay instead (2026-08-12 plan, "K-VAS: relay Battery
Info from the CSMS to KECO's real test server", §3.5/§6 rung 2). The CSMS must
already be running with KVAS_MODE=relay (see .env.example) so it forwards GetEncKey/
Battery Info to KECO's real test server instead of answering locally. The only
difference on THIS side: signData is minted by KECO for real, forwarded verbatim, so
it must verify against KECO's real `ME_Server` cert
(`KVAS_Docs/Keys/.../cn=ME_Server,...pem`), not the bench one - and there is no way
for this script to confirm its own upload was readable (only KECO holds the matching
ministry private half), so success is judged purely by `resultCode` in the Battery
Info response, not by a decoded value showing up anywhere.
"""
import argparse
import asyncio
import base64
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ocpp.v201 import ChargePoint as CPBase
from ocpp.v201 import call
from ocpp.v201.datatypes import ChargingStationType
from ocpp.v201.enums import BootReasonEnumType

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kvas import crypto  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("kvas_fake_charger")

MCU_REPO = Path(__file__).resolve().parents[2] / "SmartyPluggerIotBoard"
RECORDS_FILE = MCU_REPO / "_App" / "Kvas" / "tools" / "vas_reference_records.txt"

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_MINISTRY_CERT = REPO_ROOT / "kvas" / "bench_ministry.pem"
# --relay: KECO's real ministry cert, already sitting in this repo (byte-identical
# public test material, same as kvas/chargers/ME12345601.pem's private half is to
# CHARGER_PRIVATE_KEY_PEM below) - see the plan's TL;DR for why that's safe to use
# here. Globbed rather than hardcoded because the directory name on disk has Korean
# characters that don't round-trip cleanly through every shell/terminal encoding.
_ME_SERVER_CERT_MATCHES = list((REPO_ROOT / "KVAS_Docs" / "Keys").glob("**/*ME_Server*.pem"))

# The KECO TEST charger private key baked into KvasCredentials.h
# (CN=ME12345601 -> chargerId "ME" + "123456" + "01"). Public test material, safe to
# use here - see that file's header for provenance.
CHARGER_ID = "ME12345601"
CHARGER_PRIVATE_KEY_PEM = b"""-----BEGIN PRIVATE KEY-----
MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQQgAxyTrA7yMbO1nYzJ
P8YGfOUzfOy4A9mYC0spXbEn2o+gCgYIKoZIzj0DAQehRANCAAQLdONLeaXNdPEC
3dsltv+j6AB9WEC5vFGcxeYNBr0AfXLElSVJr3blKNHuOi3dvIUiFw09qcUH1kxN
2xwJqK5w
-----END PRIVATE KEY-----
"""


def load_records() -> list:
    if not RECORDS_FILE.exists():
        logger.error(f"reference records not found at {RECORDS_FILE} - "
                     f"is SmartyPluggerIotBoard checked out as a sibling repo?")
        sys.exit(1)
    records = []
    for line in RECORDS_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        records.append(bytes.fromhex(line))
    return records


class FakeChargePoint(CPBase):
    pass


async def run(url: str, cp_id: str, relay: bool = False):
    records = load_records()
    logger.info(f"loaded {len(records)} reference VAS record(s) from {RECORDS_FILE}")

    ws_url = f"{url.rstrip('/')}/{cp_id}"
    logger.info(f"connecting to {ws_url} ...")

    async with websockets.connect(ws_url, subprotocols=["ocpp2.0.1"]) as ws:
        cp = FakeChargePoint(cp_id, ws)
        cp_task = asyncio.ensure_future(cp.start())

        boot = await cp.call(call.BootNotification(
            charging_station=ChargingStationType(model="SmartyPlugger-Sim", vendor_name="EnerHance"),
            reason=BootReasonEnumType.power_up,
        ))
        logger.info(f"BootNotification -> status={boot.status}")

        # --- GetEncKey ---------------------------------------------------------
        get_key_resp = await cp.call(call.DataTransfer(
            vendor_id="kr.or.keco",
            message_id="GetEncKey",
            data={"chargerCnt": 1, "chargerKeySet": [{"chargerId": CHARGER_ID}]},
        ))
        logger.info(f"GetEncKey -> status={get_key_resp.status}, data={get_key_resp.data}")
        # NOTE: python-ocpp snake_cases DataTransferResponse.data on the way IN too
        # (it treats `data` as untyped Any and applies the same generic key
        # transform it uses everywhere else) - chargerKeySet arrives as
        # charger_key_set. See TODO.md's "python-ocpp's DataTransfer.data casing
        # trap" note for the general hazard this caused on the CSMS side too.
        entry = get_key_resp.data["charger_key_set"][0]
        if entry["ret_val"] != "1" or not entry["encrypt_pub"]:
            logger.error(f"GetEncKey failed: retVal={entry['ret_val']}")
            return

        key_id = entry["key_id"]
        eph_pub_der = base64.b64decode(entry["encrypt_pub"])

        # Verify the signature exactly as the MCU's KvasCrypto_VerifyServerSignature()
        # would. In local_ministry mode this proves the CSMS's bench ministry cert
        # path is wired correctly; in --relay mode the signature was minted by KECO
        # for real and forwarded verbatim, so it must verify against KECO's real
        # ME_Server cert instead (plan §3.5) - a mismatch here would mean the CSMS
        # isn't actually relaying KECO's response untouched.
        from cryptography.hazmat.primitives import hashes
        from cryptography import x509
        if relay:
            if not _ME_SERVER_CERT_MATCHES:
                logger.error("--relay: no ME_Server cert found under KVAS_Docs/Keys/ "
                             "(cn=ME_Server,...pem) - can't verify signData")
                return
            ministry_cert_path = _ME_SERVER_CERT_MATCHES[0]
            cert_label = "KECO's real ME_Server cert"
        else:
            ministry_cert_path = BENCH_MINISTRY_CERT
            cert_label = "bench_ministry.pem"
        ministry_cert = x509.load_pem_x509_certificate(ministry_cert_path.read_bytes())
        message = key_id.encode("ascii") + eph_pub_der
        try:
            ministry_cert.public_key().verify(
                base64.b64decode(entry["sign_data"]), message, ec.ECDSA(hashes.SHA256()))
            logger.info(f"signData verifies against {cert_label}: OK")
        except Exception as e:
            logger.error(f"signData verification FAILED against {cert_label}: {e}")
            return

        # Charger-side ECDH + KDF, exactly like the MCU does.
        charger_priv = serialization.load_pem_private_key(CHARGER_PRIVATE_KEY_PEM, password=None)
        eph_pub = serialization.load_der_public_key(eph_pub_der)
        z = charger_priv.exchange(ec.ECDH(), eph_pub)
        aes_key, mac_key = crypto.derive_session_keys(z)
        logger.info(f"derived session key locally for keyId={key_id}")

        # --- Battery Info (upload the 9 reference records) ---------------------
        tsdt = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        battery_set = []
        for i, content in enumerate(records):
            iv = _random_iv()
            record_bytes = crypto.encrypt_record(aes_key, mac_key, key_id.encode("ascii"), content, iv)
            battery_set.append({
                "timeStamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "sessionDuration": i * 3,
                "counter": i + 1,
                "batteryData": base64.b64encode(record_bytes).decode("ascii"),
            })

        upload_resp = await cp.call(call.DataTransfer(
            vendor_id="kr.or.keco",
            message_id="Battery Info",
            data={
                "sid": "123456", "cid": "01", "tsdt": tsdt, "keyId": key_id,
                "infoCnt": len(battery_set), "batteryDataSet": battery_set,
            },
        ))
        logger.info(f"Battery Info -> status={upload_resp.status}, data={upload_resp.data}")
        result_code = upload_resp.data.get("result_code")
        if result_code != "0":
            logger.error(f"resultCode={result_code!r} != '0' - see TODO.md's data=\"\" note "
                         f"if this is an empty string instead of an object")
        elif relay:
            # --relay: this script has no way to confirm KECO could actually read
            # the data back (only KECO holds the matching ministry private half) -
            # resultCode "0" from KECO IS the success signal here, full stop (plan
            # §3.5). The GUI may additionally show decoded values if D8's
            # shadow-decrypt is active for this charger (kvas/shadow_decrypt.py) -
            # that's a bonus, not what "success" is judged on.
            logger.info(f"{len(battery_set)} record(s) uploaded and ACCEPTED by KECO "
                        f"(resultCode 0) - check http://localhost:1234 for relay status")
        else:
            logger.info(f"{len(battery_set)} record(s) uploaded and accepted - "
                        f"check the GUI at http://localhost:1234 for decoded values")

        cp_task.cancel()


def _random_iv() -> bytes:
    import os
    return os.urandom(16)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:9000")
    parser.add_argument("--cp-id", default="TEST_CP_016")
    parser.add_argument("--relay", action="store_true",
                         help="exercise KVAS_MODE=relay: verify signData against KECO's real "
                              "ME_Server cert instead of bench_ministry.pem. The CSMS itself "
                              "must already be running with KVAS_MODE=relay (see .env.example) "
                              "- this flag only changes what THIS script expects back.")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.url, args.cp_id, relay=args.relay))
    except KeyboardInterrupt:
        pass
