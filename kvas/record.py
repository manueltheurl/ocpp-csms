"""
kvas/record.py - decodes a K-VAS "Battery Data Exchange" TLV record (the plaintext
that comes out of AES-CBC decryption) into scaled physical values.

Wire format authority: SmartyPlugger's
`.claude/docs/kvas-vas-record-format.md` §2-4. This module is the CSMS-side twin of
that repo's `_App/Kvas/KvasVasChannel.c` `TryParseRecord()` - same tag table, same
scale factors - but tolerant rather than strict: a malformed or unexpected-order
record is reported back as a dict, never raised, because a decode failure here must
show up as "undecryptable"/"parse_error" in the GUI, not crash the OCPP handler.
"""

# tag -> (group letter, name, value type, fixed length or None for variable,
#         decoder(value_bytes) -> physical value)
_INVALID_U8 = 0xFF
_INVALID_U16 = 0xFFFF


def _u16(b):
    return (b[0] << 8) | b[1]


def _u32(b):
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def _decode_a1(v):
    return {"timestamp": _u32(v)}


def _decode_b1(v):
    return {"session_duration_s": _u32(v)}


def _decode_c1(v):
    return {"counter": _u16(v)}


def _decode_a2(v):
    vin = v.decode("ascii", errors="replace")
    return {"vin": None if v == b"\x00" * 17 else vin}


def _decode_b2(v):
    return {"battery_id": v.decode("ascii", errors="replace")}


def _decode_c2(v):
    # 32 B = encrypted VIN only; 97 B = encrypted VIN(32) || uncompressed SEC1 EC
    # point(65, leading 0x04). Opaque either way - we do not hold the key that
    # encrypts this. Shown as hex in the GUI (open item, see the plan §7).
    return {"encrypted_vin_hex": v.hex(), "encrypted_vin_len": len(v)}


def _decode_a3(v):
    raw = v[0]
    return {"soc_percent": None if raw == _INVALID_U8 else raw * 0.5}


def _decode_a4(v):
    raw = v[0]
    return {"soh_percent": None if raw == _INVALID_U8 else raw * 1.0}


def _decode_a5(v):
    raw = _u16(v)
    return {"pack_current_a": None if raw == _INVALID_U16 else raw * 0.1}


def _decode_a6(v):
    raw = _u16(v)
    return {"pack_voltage_v": None if raw == _INVALID_U16 else raw * 0.1}


def _decode_a7(v):
    cells = [None if b == _INVALID_U8 else b * 0.02 for b in v]
    valid = [c for c in cells if c is not None]
    return {
        "cell_voltages_v": cells,
        "cell_voltage_max_v": max(valid) if valid else None,
        "cell_voltage_min_v": min(valid) if valid else None,
    }


def _decode_b7(v):
    mx, mn = v[0], v[1]
    return {
        "cell_voltage_max_v": None if mx == _INVALID_U8 else mx * 0.02,
        "cell_voltage_min_v": None if mn == _INVALID_U8 else mn * 0.02,
    }


def _decode_a8(v):
    temps = [None if b == _INVALID_U8 else b - 40 for b in v]
    valid = [t for t in temps if t is not None]
    return {
        "cell_temps_c": temps,
        "cell_temp_max_c": max(valid) if valid else None,
        "cell_temp_min_c": min(valid) if valid else None,
    }


def _decode_b8(v):
    mx, mn = v[0], v[1]
    return {
        "cell_temp_max_c": None if mx == _INVALID_U8 else mx - 40,
        "cell_temp_min_c": None if mn == _INVALID_U8 else mn - 40,
    }


# tag -> (group, fixed_len_or_None, decoder)
_TAGS = {
    0xA1: ("A", 4, _decode_a1),
    0xB1: ("A", 4, _decode_b1),
    0xC1: ("A", 2, _decode_c1),
    0xA2: ("B", 17, _decode_a2),
    0xB2: ("B", None, _decode_b2),
    0xC2: ("B", None, _decode_c2),   # 32 or 97, validated below
    0xA3: ("C", 1, _decode_a3),
    0xA4: ("D", 1, _decode_a4),
    0xA5: ("E", 2, _decode_a5),
    0xA6: ("F", 2, _decode_a6),
    0xA7: ("G", None, _decode_a7),   # u16 length, the only one
    0xB7: ("G", 2, _decode_b7),
    0xA8: ("H", None, _decode_a8),
    0xB8: ("H", 2, _decode_b8),
}

_CASE_1_GROUPS = "ABCDEFGH"
_CASE_2_PERIODIC_GROUPS = "ABC"


def decode(content: bytes) -> dict:
    """Decodes the plaintext K-VAS content bytes. Always returns a dict - a
    malformed record comes back as {"parse_error": "...", "raw_hex": "..."} rather
    than raising, per the plan's Stage 0.3 (0.3: 'must tolerate garbage')."""
    out = {"raw_hex": content.hex(), "tags": {}, "warnings": []}
    pos = 0
    groups_seen = []

    try:
        while pos < len(content):
            if pos + 2 > len(content):
                raise ValueError(f"truncated TLV header at offset {pos}")
            tag = content[pos]
            if tag not in _TAGS:
                raise ValueError(f"unknown tag 0x{tag:02X} at offset {pos}")
            group, fixed_len, decoder = _TAGS[tag]

            if tag == 0xA7:
                if pos + 3 > len(content):
                    raise ValueError("truncated 0xA7 length field")
                length = _u16(content[pos + 1:pos + 3])
                value_start = pos + 3
            else:
                length = content[pos + 1]
                value_start = pos + 2

            value_end = value_start + length
            if value_end > len(content):
                raise ValueError(f"tag 0x{tag:02X} claims {length} bytes, only "
                                  f"{len(content) - value_start} available")
            value = content[value_start:value_end]

            if fixed_len is not None and length != fixed_len:
                raise ValueError(f"tag 0x{tag:02X} length {length}, expected {fixed_len}")
            if tag == 0xC2 and length not in (32, 97):
                raise ValueError(f"tag 0xC2 length {length}, expected 32 or 97")

            out["tags"].update(decoder(value))
            groups_seen.append(group)
            pos = value_end

        composition = "".join(groups_seen)
        if composition not in (_CASE_1_GROUPS, _CASE_2_PERIODIC_GROUPS):
            out["warnings"].append(
                f"unexpected group composition '{composition}' "
                f"(expected 'ABCDEFGH' or 'ABC')")
        out["is_full_frame"] = composition == _CASE_1_GROUPS

        soc = out["tags"].get("soc_percent")
        soh = out["tags"].get("soh_percent")
        if soc is not None and not (0 <= soc <= 100):
            out["warnings"].append(f"SoC {soc}% out of 0..100 range")
        if soh is not None and not (0 <= soh <= 100):
            out["warnings"].append(f"SoH {soh}% out of 0..100 range")

        return out
    except (ValueError, IndexError) as e:
        return {"parse_error": str(e), "raw_hex": content.hex()}
