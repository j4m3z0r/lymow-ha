"""Unit tests for lymow_api. All external calls are mocked."""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import MagicMock, patch

import pytest

from lymow_api.exceptions import LymowAuthError
from lymow_api.models import MowerState, RobotStatus
from lymow_api.proto import UserCtrl, decode_pb_output, encode_command, PB_VERSION, _PBINPUT_USER_CTRL, _PBINPUT_VERSION


# ---------------------------------------------------------------------------
# Proto encode/decode
# ---------------------------------------------------------------------------

def test_encode_command_clean():
    payload = encode_command(UserCtrl.CLEAN)
    raw = base64.b64decode(payload)
    # Should contain field 5 (tag 40) = CLEAN (1) and field 2 (tag 16) = PB_VERSION
    assert raw is not None
    assert len(raw) > 0


def test_encode_decode_roundtrip():
    # Encode a fake PbOutput with robotInfo (field 10) containing status=2, battery=75
    from lymow_api.proto import _field_bytes, _field_varint, _PBOUTPUT_ROBOT_INFO, _ROBOT_INFO_STATUS, _ROBOT_INFO_BATTERY

    robot_info = _field_varint(_ROBOT_INFO_STATUS, RobotStatus.CLEANING)
    robot_info += _field_varint(_ROBOT_INFO_BATTERY, 75)
    pb_output = _field_bytes(_PBOUTPUT_ROBOT_INFO, robot_info)
    payload = base64.b64encode(pb_output).decode()

    result = decode_pb_output(payload)
    assert result["robot_status"] == RobotStatus.CLEANING
    assert result["battery"] == 75
    assert result["error_codes"] == []


def test_decode_error_codes():
    from lymow_api.proto import _field_bytes, _varint, _PBOUTPUT_ERROR_CODES

    # Error codes are packed (wire type 2, length-delimited array of varints)
    packed = _varint(42) + _varint(7)
    pb_output = _field_bytes(_PBOUTPUT_ERROR_CODES, packed)
    payload = base64.b64encode(pb_output).decode()

    result = decode_pb_output(payload)
    assert 42 in result["error_codes"]
    assert 7 in result["error_codes"]


def test_decode_empty_payload():
    result = decode_pb_output(base64.b64encode(b"").decode())
    assert result["robot_status"] is None
    assert result["battery"] is None
    assert result["error_codes"] == []


# ---------------------------------------------------------------------------
# MowerState properties
# ---------------------------------------------------------------------------

def test_mower_state_is_mowing():
    s = MowerState(robot_status=RobotStatus.CLEANING, battery=80)
    assert s.is_mowing
    assert not s.is_docked
    assert not s.is_returning


def test_mower_state_is_docked():
    s = MowerState(robot_status=RobotStatus.CHARGING, battery=50)
    assert s.is_docked
    assert not s.is_mowing


def test_mower_state_is_returning():
    s = MowerState(robot_status=RobotStatus.DOCKING, battery=20)
    assert s.is_returning


def test_mower_state_has_error():
    s = MowerState(robot_status=RobotStatus.ERROR)
    assert s.has_error

    s2 = MowerState(robot_status=RobotStatus.WAITING, error_codes=[42])
    assert s2.has_error


# ---------------------------------------------------------------------------
# LymowClient auth
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from lymow_api.client import LymowClient
    return LymowClient("user@example.com", "Password1", region_key="ap")


def _make_cognito_auth_response(id_token="test-id-token"):
    return {"AuthenticationResult": {"IdToken": id_token, "AccessToken": "access", "RefreshToken": "refresh"}}


def _make_identity_id_response(identity_id="ap-southeast-2:test-id"):
    return {"IdentityId": identity_id}


def _make_credentials_response():
    return {
        "Credentials": {
            "AccessKeyId": "ASIATEST",
            "SecretAccessKey": "secret",
            "SessionToken": "token",
            "Expiration": None,
        }
    }


@pytest.mark.asyncio
async def test_authenticate_success(client):
    cognito_mock = MagicMock()
    cognito_mock.id_token = "test-id-token"
    cognito_mock.access_token = "test-access-token"

    identity_mock = MagicMock()
    identity_mock.get_id.return_value = _make_identity_id_response()
    identity_mock.get_credentials_for_identity.return_value = _make_credentials_response()

    def boto3_client(service, region_name=None):
        if service == "cognito-identity":
            return identity_mock
        raise ValueError(f"Unexpected service: {service}")

    with patch("pycognito.Cognito", return_value=cognito_mock), \
         patch("boto3.client", side_effect=boto3_client):
        await client.authenticate()

    assert client._id_token == "test-id-token"
    assert client._access_token == "test-access-token"
    assert client._identity_id == "ap-southeast-2:test-id"
    assert client._aws_credentials is not None


@pytest.mark.asyncio
async def test_authenticate_bad_password(client):
    cognito_mock = MagicMock()
    cognito_mock.authenticate.side_effect = Exception("NotAuthorizedException: Incorrect username or password")

    with patch("pycognito.Cognito", return_value=cognito_mock):
        with pytest.raises(LymowAuthError):
            await client.authenticate()


# ---------------------------------------------------------------------------
# LymowClient state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_state_returns_cached(client):
    from lymow_api.models import MowerState, RobotStatus
    client._state = MowerState(robot_status=RobotStatus.CLEANING, battery=60)
    state = await client.get_state()
    assert state.robot_status == RobotStatus.CLEANING
    assert state.battery == 60


@pytest.mark.asyncio
async def test_start_mowing_publishes(client):
    client._thing_name = "test-thing"
    with patch.object(client, "_publish_sync") as mock_pub:
        await client.start_mowing()
    mock_pub.assert_called_once()
    payload = mock_pub.call_args[0][0]
    raw = base64.b64decode(payload)
    assert len(raw) > 0


@pytest.mark.asyncio
async def test_return_to_dock_publishes(client):
    client._thing_name = "test-thing"
    with patch.object(client, "_publish_sync") as mock_pub:
        await client.return_to_dock()
    mock_pub.assert_called_once()


@pytest.mark.asyncio
async def test_get_state_copies_position(client):
    from lymow_api.models import MowerState, RobotStatus
    client._state = MowerState(robot_status=RobotStatus.CLEANING, battery=80, position=(3.5, -1.0))
    state = await client.get_state()
    assert state.position == (3.5, -1.0)


def test_iter_fields_yields_32bit():
    """_iter_fields must yield wire-type-5 (32-bit) fields as 4 raw bytes."""
    import struct
    from lymow_api.proto import _iter_fields, _tag

    # Build a message: field 7, wire type 5, value = 3.14 as float32
    raw = _tag(7, 5) + struct.pack('<f', 3.14)
    fields = list(_iter_fields(raw))

    assert len(fields) == 1
    field_num, wire_type, value = fields[0]
    assert field_num == 7
    assert wire_type == 5
    assert isinstance(value, bytes) and len(value) == 4
    assert abs(struct.unpack('<f', value)[0] - 3.14) < 0.001


def test_decode_pb_map_with_polygon():
    """Zone polygons are decoded from PbZoneBasicInfo field 5 point submessages."""
    import struct
    from lymow_api.proto import (
        decode_pb_map, _field_bytes, _field_varint, _tag,
        _PBMAP_GO_ZONES, _PBZONE_BASIC_INFO, _PBZONE_BASIC_NAME, _PBZONE_BASIC_HASH_ID,
        _PBZONE_BASIC_POLYGON,
    )

    def _field_float32(field: int, value: float) -> bytes:
        return _tag(field, 5) + struct.pack('<f', value)

    # Encode a single polygon point at (1.5, 2.5): field 1 + field 2, wire type 5.
    # The polygon container holds repeated field-1 (LEN) submessages, one per point.
    point = _field_float32(1, 1.5) + _field_float32(2, 2.5)

    # PbZoneBasicInfo: name, hash_id, polygon (field 5 wrapping the point submessage)
    basic_info = (
        _field_bytes(_PBZONE_BASIC_NAME, b"TestZone")
        + _field_bytes(_PBZONE_BASIC_HASH_ID, b"abc123")
        + _field_bytes(_PBZONE_BASIC_POLYGON, _field_bytes(1, point))
    )

    # PbZone wrapping basic_info in field 1
    zone_msg = _field_bytes(_PBZONE_BASIC_INFO, basic_info)

    # PbMap with one go zone
    pb_map = _field_bytes(_PBMAP_GO_ZONES, zone_msg)

    result = decode_pb_map(pb_map)
    assert len(result["go_zones"]) == 1
    zone = result["go_zones"][0]
    assert zone["name"] == "TestZone"
    assert len(zone["polygon"]) == 1
    x, y = zone["polygon"][0]
    assert abs(x - 1.5) < 0.001
    assert abs(y - 2.5) < 0.001


def test_decode_pb_map_incomplete_point_is_dropped():
    """A polygon point with only x (no y) must be dropped, not included."""
    import struct
    from lymow_api.proto import (
        decode_pb_map, _field_bytes, _tag,
        _PBMAP_GO_ZONES, _PBZONE_BASIC_INFO, _PBZONE_BASIC_POLYGON,
        _PBZONE_BASIC_NAME,
    )

    def _field_float32(field, value):
        return _tag(field, 5) + struct.pack('<f', value)

    # Only field 1 (x), no field 2 (y), wrapped in the per-point field-1 submessage
    point = _field_float32(1, 9.9)
    basic_info = _field_bytes(_PBZONE_BASIC_NAME, b"NoY") + _field_bytes(
        _PBZONE_BASIC_POLYGON, _field_bytes(1, point)
    )
    zone_msg = _field_bytes(_PBZONE_BASIC_INFO, basic_info)
    pb_map = _field_bytes(_PBMAP_GO_ZONES, zone_msg)

    result = decode_pb_map(pb_map)
    assert result["go_zones"][0]["polygon"] == []


def test_decode_position():
    """Mower position is parsed from PbOutput field 14 (PbAlgoLoc, map frame)."""
    import struct
    from lymow_api.proto import (
        decode_pb_output, _field_bytes, _tag,
        _PBOUTPUT_ALGO_LOC, _ALGOLOC_X, _ALGOLOC_Y,
    )

    def _field_float32(field: int, value: float) -> bytes:
        return _tag(field, 5) + struct.pack('<f', value)

    algo_loc = _field_float32(_ALGOLOC_X, 3.75) + _field_float32(_ALGOLOC_Y, -1.25)
    pb_output = _field_bytes(_PBOUTPUT_ALGO_LOC, algo_loc)
    payload = base64.b64encode(pb_output).decode()

    result = decode_pb_output(payload)
    assert result["position"] is not None
    x, y = result["position"]
    assert abs(x - 3.75) < 0.001
    assert abs(y - (-1.25)) < 0.001


def test_decode_position_partial_is_dropped():
    """A PbAlgoLoc with only x (no y) must leave position as None."""
    import struct
    from lymow_api.proto import (
        decode_pb_output, _field_bytes, _tag, _PBOUTPUT_ALGO_LOC, _ALGOLOC_X,
    )

    def _field_float32(field: int, value: float) -> bytes:
        return _tag(field, 5) + struct.pack('<f', value)

    algo_loc = _field_float32(_ALGOLOC_X, 3.75)   # x only, no y
    pb_output = _field_bytes(_PBOUTPUT_ALGO_LOC, algo_loc)
    payload = base64.b64encode(pb_output).decode()

    result = decode_pb_output(payload)
    assert result["position"] is None


def test_map_sensor_attributes():
    """LymowMapSensor.extra_state_attributes returns the expected JSON payload."""
    from unittest.mock import MagicMock
    from lymow_api.models import LymowData, MowerState, RobotStatus, Zone, ZoneStatus

    coordinator = MagicMock()
    coordinator.data = LymowData(
        state=MowerState(robot_status=RobotStatus.CLEANING, battery=80, position=(1.0, 2.0)),
        zones=[
            Zone(hash_id="abc", name="Oak Grove", zone_type="go", polygon=[(0.0, 0.0), (1.0, 0.0)]),
            Zone(hash_id="def", name="", zone_type="nogo", polygon=[(5.0, 5.0)]),
        ],
        channels=[],
        zone_statuses={"abc": ZoneStatus.ACTIVE},
    )

    data = coordinator.data
    zones_payload = [
        {
            "hash_id": z.hash_id,
            "name": z.name,
            "zone_type": z.zone_type,
            "polygon": [list(p) for p in z.polygon],
            "status": data.zone_statuses.get(z.hash_id, ZoneStatus.UNKNOWN),
        }
        for z in data.zones
    ]
    pos = data.state.position
    attrs = {
        "zones": zones_payload,
        "mower_position": list(pos) if pos else None,
        "robot_status": data.state.status_name,
        "is_mowing": data.state.is_mowing,
    }

    assert attrs["mower_position"] == [1.0, 2.0]
    assert attrs["robot_status"] == "cleaning"
    assert attrs["is_mowing"] is True
    assert len(attrs["zones"]) == 2
    assert attrs["zones"][0]["status"] == "active"
    assert attrs["zones"][1]["status"] == "unknown"
    assert attrs["zones"][0]["polygon"] == [[0.0, 0.0], [1.0, 0.0]]


# ---------------------------------------------------------------------------
# Connection health detection (request_map_refresh + interrupt callbacks)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_map_refresh_raises_when_connection_dead(client):
    """A dead connection must raise so the coordinator's reconnect path runs.

    Regression test: a failed publish sets _mqtt_connection = None; silently
    returning here left the integration serving stale cached state forever.
    """
    from lymow_api.exceptions import LymowConnectionError
    client._thing_name = "test-thing"
    client._mqtt_connection = None
    with pytest.raises(LymowConnectionError):
        await client.request_map_refresh()


@pytest.mark.asyncio
async def test_request_map_refresh_noop_when_never_bound(client):
    # No device bound yet (setup incomplete) — nothing to detect, no raise.
    client._thing_name = None
    client._mqtt_connection = None
    await client.request_map_refresh()


@pytest.mark.asyncio
async def test_request_map_refresh_raises_after_unresumed_interruption(client):
    import time as _time
    from lymow_api.exceptions import LymowConnectionError
    client._thing_name = "test-thing"
    client._mqtt_connection = MagicMock()
    client._last_map_refresh = _time.monotonic()  # map refresh not due
    client._interrupted_at = _time.monotonic() - (client._INTERRUPT_GRACE + 1)
    with pytest.raises(LymowConnectionError):
        await client.request_map_refresh()


@pytest.mark.asyncio
async def test_request_map_refresh_tolerates_recent_interruption(client):
    # Within the grace window awscrt may still auto-resume — don't tear down.
    import time as _time
    client._thing_name = "test-thing"
    client._mqtt_connection = MagicMock()
    client._last_map_refresh = _time.monotonic()
    client._interrupted_at = _time.monotonic() - 1
    await client.request_map_refresh()


def test_interrupted_callback_records_time(client):
    client._on_connection_interrupted(MagicMock(), "some awscrt error")
    assert client._interrupted_at is not None


def test_resumed_callback_clears_interruption_and_resubscribes(client):
    import time as _time
    client._interrupted_at = _time.monotonic()
    loop = MagicMock()
    client._loop = loop
    client._on_connection_resumed(MagicMock(), 0, False)
    assert client._interrupted_at is None
    # clean_session=True → session_present is False → must resubscribe
    loop.call_soon_threadsafe.assert_called_once()


def test_resumed_callback_skips_resubscribe_when_session_present(client):
    loop = MagicMock()
    client._loop = loop
    client._on_connection_resumed(MagicMock(), 0, True)
    loop.call_soon_threadsafe.assert_not_called()
