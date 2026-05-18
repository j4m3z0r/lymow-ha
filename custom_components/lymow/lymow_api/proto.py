"""Minimal protobuf encode/decode for Lymow PbInput/PbOutput messages."""
import base64
import logging
import struct

# Wire types
_WT_VARINT = 0
_WT_LEN = 2
_WT_32BIT = 5
_WT_64BIT = 1

# PbInput field numbers (verified from decompiled Hermes bytecode)
_PBINPUT_USER_CTRL = 5   # tag 40
_PBINPUT_VERSION = 2     # tag 16
_PBINPUT_MAP = 12        # tag 98
_PBINPUT_BT_MAP = 23     # tag 186 (length-delimited PbBtMap)

# PbBtMap field numbers (same in both PbInput and PbOutput)
_PBBTMAP_QUERY_INDEX = 1  # tag 8 (varint)
_PBBTMAP_QUERY_ACK = 2    # tag 18 (length-delimited PbQueryAck) — in PbOutput responses
_PBBTMAP_QUERY_PATH = 3   # tag 24 (bool)
_PBBTMAP_QUERY_MAP = 4    # tag 32 (bool/varint)

# PbQueryAck field numbers (inside btMap in PbOutput responses)
_PBQUERYACK_TOTAL_PACKET = 1  # tag 8 (int32)
_PBQUERYACK_PACKET_INDEX = 2  # tag 16 (int32)
_PBQUERYACK_MAP_DATA = 3      # tag 26 (bytes — contains PbMap data)

# PbOutput field numbers (verified from decompiled Hermes bytecode)
# There is NO top-level map field — all map data comes via btMap (field 23).
_PBOUTPUT_ERROR_CODES = 3    # tag 26 (packed int32 array)
_PBOUTPUT_WARNING_CODES = 4  # tag 34 (packed int32 array)
_PBOUTPUT_ROBOT_INFO = 5     # tag 42 (length-delimited PbRobotInfo) — verified via MITM
_PBOUTPUT_LOCALIZATION_INFO = 6  # tag 50 — raw odometry, NOT map-frame position
_PBOUTPUT_ALGO_LOC = 14      # tag 114 (length-delimited) — map-frame position, verified live
_PBOUTPUT_BT_MAP = 23        # tag 186 (length-delimited PbBtMap) — carries map data

# PbAlgoLoc field numbers (field 14 in PbOutput — map-frame position in metres)
_ALGOLOC_X = 1               # wire type 5, float32, east metres
_ALGOLOC_Y = 2               # wire type 5, float32, north metres
_ALGOLOC_HEADING = 3         # wire type 5, float32, radians

# PbRobotInfo field numbers
_ROBOT_INFO_STATUS = 1
_ROBOT_INFO_BATTERY = 2

# PbMap field numbers (verified from decompiled Hermes bytecode)
_PBMAP_GO_ZONES = 1    # tag 10 (repeated PbZone)
_PBMAP_NOGO_ZONES = 2  # tag 18 (repeated PbZone)
_PBMAP_CHANNELS = 3    # tag 26 (repeated PbChannel — connector polylines between zones)

# PbZone field numbers
_PBZONE_BASIC_INFO = 1

# PbZoneBasicInfo field numbers
_PBZONE_BASIC_NAME = 2
_PBZONE_BASIC_HASH_ID = 3
_PBZONE_BASIC_POLYGON = 5   # repeated point submessages in PbZoneBasicInfo

# PbZonePoint field numbers (submessages in polygon field)
_PBZONE_POINT_X = 1         # field 1, wire type 5, float32 = x (east, metres)
_PBZONE_POINT_Y = 2         # field 2, wire type 5, float32 = y (north, metres)


# Protocol version constant (PB_VERSION_4_9)
PB_VERSION = 49


class UserCtrl:
    NONE = 0
    CLEAN = 1
    DOCK = 2
    PAUSE = 3
    RESUME = 4
    GO_ZONE_PARTITION = 5
    NO_GO_ZONE_PARTITION = 6
    EXIT_ZONE_PARTITION = 7
    CLEAR_ZONE = 8
    LOCK = 18
    QUERY_MAP = 19
    QUERY_SCHEDULES = 20
    PAUSE_DOCK = 21
    RESUME_DOCK = 22
    FORCE_REINIT = 28
    RECHARGE_DOCK = 33
    SET_TASK_CONFIG = 36
    START_MOW_SCHEDULE = 46


def _varint(n: int) -> bytes:
    out = []
    while True:
        bits = n & 0x7F
        n >>= 7
        if n:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            break
    return bytes(out)


def _tag(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _field_varint(field: int, value: int) -> bytes:
    return _tag(field, _WT_VARINT) + _varint(value)


def _field_bytes(field: int, data: bytes) -> bytes:
    return _tag(field, _WT_LEN) + _varint(len(data)) + data


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("Truncated varint")


def _iter_fields(data: bytes):
    pos = 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == _WT_VARINT:
            value, pos = _read_varint(data, pos)
            yield field_num, wire_type, value
        elif wire_type == _WT_LEN:
            length, pos = _read_varint(data, pos)
            value = data[pos : pos + length]
            pos += length
            yield field_num, wire_type, value
        elif wire_type == _WT_64BIT:  # 64-bit
            value = data[pos : pos + 8]
            pos += 8
            yield field_num, wire_type, value
        elif wire_type == _WT_32BIT:  # 32-bit
            value = data[pos : pos + 4]
            pos += 4
            yield field_num, wire_type, value
        else:
            return


def _encode_bt_map(query_index: int = 0, query_map: bool = False) -> bytes:
    msg = _field_varint(_PBBTMAP_QUERY_INDEX, query_index)
    if query_map:
        msg += _field_varint(_PBBTMAP_QUERY_MAP, 1)
    return msg


def encode_command(user_ctrl: int) -> str:
    """Encode a simple command PbInput and return base64 string for MQTT publish."""
    msg = _field_varint(_PBINPUT_USER_CTRL, user_ctrl)
    msg += _field_varint(_PBINPUT_VERSION, PB_VERSION)
    return base64.b64encode(msg).decode()


def encode_start_zone(zone_hash_ids: list[str]) -> str:
    """Encode CLEAN targeting specific zones via PbInput.map (field 12).

    Sends UserCtrl.CLEAN with a PbMap containing only the selected goZones,
    each identified by hash_id. Device ignores zones not in this list.
    """
    pb_map = b""
    for hash_id in zone_hash_ids:
        basic_info = _field_bytes(_PBZONE_BASIC_HASH_ID, hash_id.encode())
        zone = _field_bytes(_PBZONE_BASIC_INFO, basic_info)
        pb_map += _field_bytes(_PBMAP_GO_ZONES, zone)
    msg = _field_varint(_PBINPUT_USER_CTRL, UserCtrl.CLEAN)
    msg += _field_varint(_PBINPUT_VERSION, PB_VERSION)
    msg += _field_bytes(_PBINPUT_MAP, pb_map)
    return base64.b64encode(msg).decode()


def encode_query_map() -> str:
    """Encode QUERY_MAP PbInput — requires btMap field or device ignores it."""
    bt_map = _encode_bt_map(query_index=0, query_map=True)
    msg = _field_varint(_PBINPUT_USER_CTRL, UserCtrl.QUERY_MAP)
    msg += _field_varint(_PBINPUT_VERSION, PB_VERSION)
    msg += _field_bytes(_PBINPUT_BT_MAP, bt_map)
    return base64.b64encode(msg).decode()


def encode_get_map_data(packet_index: int) -> str:
    """Encode a subsequent map packet request (multi-packet map fetch)."""
    bt_map = _encode_bt_map(query_index=packet_index, query_map=True)
    msg = _field_varint(_PBINPUT_VERSION, PB_VERSION)
    msg += _field_bytes(_PBINPUT_BT_MAP, bt_map)
    return base64.b64encode(msg).decode()


def _decode_zone_basic_info(data: bytes) -> dict:
    info: dict = {"name": "", "hash_id": "", "polygon": []}
    _logged_first = False
    for field, wt, value in _iter_fields(data):
        if field == _PBZONE_BASIC_NAME and wt == _WT_LEN:
            info["name"] = value.decode("utf-8", errors="replace")
        elif field == _PBZONE_BASIC_HASH_ID and wt == _WT_LEN:
            info["hash_id"] = value.decode("utf-8", errors="replace")
        elif field == _PBZONE_BASIC_POLYGON and wt == _WT_LEN:
            # polygon container holds repeated field-1 (LEN) submessages, one per point
            for f2, wt2, v2 in _iter_fields(value):
                if f2 == 1 and wt2 == _WT_LEN:
                    x = y = None
                    for f3, wt3, v3 in _iter_fields(v2):
                        if f3 == _PBZONE_POINT_X and wt3 == _WT_32BIT:
                            x = struct.unpack('<f', v3)[0]
                        elif f3 == _PBZONE_POINT_Y and wt3 == _WT_32BIT:
                            y = struct.unpack('<f', v3)[0]
                    if x is not None and y is not None:
                        info["polygon"].append((x, y))
    return info


def _decode_zone(data: bytes) -> dict | None:
    for field, wt, value in _iter_fields(data):
        if field == _PBZONE_BASIC_INFO and wt == _WT_LEN:
            return _decode_zone_basic_info(value)
    return None


def _decode_channel(data: bytes) -> dict | None:
    """Decode a PbChannel message — a connector polyline between zones.

    Field 5 holds the waypoints in the same nested format as PbZoneBasicInfo polygon.
    """
    points: list[tuple[float, float]] = []
    for field, wt, value in _iter_fields(data):
        if field == _PBZONE_BASIC_POLYGON and wt == _WT_LEN:
            for f2, wt2, v2 in _iter_fields(value):
                if f2 == 1 and wt2 == _WT_LEN:
                    x = y = None
                    for f3, wt3, v3 in _iter_fields(v2):
                        if f3 == _PBZONE_POINT_X and wt3 == _WT_32BIT:
                            x = struct.unpack('<f', v3)[0]
                        elif f3 == _PBZONE_POINT_Y and wt3 == _WT_32BIT:
                            y = struct.unpack('<f', v3)[0]
                    if x is not None and y is not None:
                        points.append((x, y))
    return {"points": points} if points else None


def decode_pb_map(data: bytes) -> dict:
    """Decode a PbMap from raw bytes (concatenated mapData packets from btMap)."""
    result: dict = {"go_zones": [], "nogo_zones": [], "channels": []}
    for field, wt, value in _iter_fields(data):
        if field == _PBMAP_GO_ZONES and wt == _WT_LEN:
            zone = _decode_zone(value)
            if zone:
                result["go_zones"].append(zone)
        elif field == _PBMAP_NOGO_ZONES and wt == _WT_LEN:
            zone = _decode_zone(value)
            if zone:
                result["nogo_zones"].append(zone)
        elif field == _PBMAP_CHANNELS and wt == _WT_LEN:
            channel = _decode_channel(value)
            if channel:
                result["channels"].append(channel)
    return result


def _decode_query_ack(data: bytes) -> dict:
    """Decode PbQueryAck from btMap field in PbOutput."""
    ack: dict = {"total_packet": 0, "packet_index": 0, "map_data": b""}
    for field, wt, value in _iter_fields(data):
        if field == _PBQUERYACK_TOTAL_PACKET and wt == _WT_VARINT:
            ack["total_packet"] = value
        elif field == _PBQUERYACK_PACKET_INDEX and wt == _WT_VARINT:
            ack["packet_index"] = value
        elif field == _PBQUERYACK_MAP_DATA and wt == _WT_LEN:
            ack["map_data"] = value
    return ack


def decode_pb_output(payload: str | bytes) -> dict:
    """Decode a PbOutput message from the device.

    The MQTT payload format is JSON: {"message":"<base64_protobuf>"}
    Falls back to treating the payload as raw base64 for compatibility.
    """
    import json as _json
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    try:
        envelope = _json.loads(payload)
        raw = base64.b64decode(envelope["message"])
    except Exception:
        # Fallback: treat whole payload as raw base64 protobuf bytes
        raw = base64.b64decode(payload)

    result: dict = {
        "robot_status": None,
        "battery": None,
        "error_codes": [],
        "warning_codes": [],
        "bt_map": None,  # dict with query_ack if present
        "position": None,
        "heading": None,
    }

    for field, wt, value in _iter_fields(raw):
        if field == _PBOUTPUT_ROBOT_INFO and wt == _WT_LEN:
            for f2, wt2, v2 in _iter_fields(value):
                if f2 == _ROBOT_INFO_STATUS and wt2 == _WT_VARINT:
                    result["robot_status"] = v2
                elif f2 == _ROBOT_INFO_BATTERY and wt2 == _WT_VARINT:
                    result["battery"] = v2
        elif field == _PBOUTPUT_ERROR_CODES and wt == _WT_LEN:
            pos = 0
            while pos < len(value):
                v, pos = _read_varint(value, pos)
                result["error_codes"].append(v)
        elif field == _PBOUTPUT_WARNING_CODES and wt == _WT_LEN:
            pos = 0
            while pos < len(value):
                v, pos = _read_varint(value, pos)
                result["warning_codes"].append(v)
        elif field == _PBOUTPUT_BT_MAP and wt == _WT_LEN:
            bt_map: dict = {"query_ack": None, "query_map": False, "query_path": False}
            for f2, wt2, v2 in _iter_fields(value):
                if f2 == _PBBTMAP_QUERY_ACK and wt2 == _WT_LEN:
                    bt_map["query_ack"] = _decode_query_ack(v2)
                elif f2 == _PBBTMAP_QUERY_MAP and wt2 == _WT_VARINT:
                    bt_map["query_map"] = bool(v2)
                elif f2 == _PBBTMAP_QUERY_PATH and wt2 == _WT_VARINT:
                    bt_map["query_path"] = bool(v2)
            result["bt_map"] = bt_map
        elif field == _PBOUTPUT_ALGO_LOC and wt == _WT_LEN:
            x = y = heading = None
            for f2, wt2, v2 in _iter_fields(value):
                if f2 == _ALGOLOC_X and wt2 == _WT_32BIT:
                    x = struct.unpack('<f', v2)[0]
                elif f2 == _ALGOLOC_Y and wt2 == _WT_32BIT:
                    y = struct.unpack('<f', v2)[0]
                elif f2 == _ALGOLOC_HEADING and wt2 == _WT_32BIT:
                    heading = struct.unpack('<f', v2)[0]
            if x is not None and y is not None:
                result["position"] = (x, y)
                result["heading"] = heading

    return result
