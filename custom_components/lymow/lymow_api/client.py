"""LymowClient — async client for the Lymow cloud API."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Callable

import boto3

from .exceptions import LymowApiError, LymowAuthError, LymowConnectionError
from .models import Channel, MowerState, RobotStatus, Zone
from .proto import (
    UserCtrl,
    decode_pb_output,
    decode_pb_map,
    encode_command,
    encode_get_map_data,
    encode_query_map,
    encode_start_zone,
)
from .regions import REGIONS, RegionConfig

_LOGGER = logging.getLogger(__name__)


class LymowClient:
    def __init__(
        self,
        username: str,
        password: str,
        region_key: str = "ap",
    ) -> None:
        if region_key not in REGIONS:
            raise ValueError(f"Unknown region: {region_key!r}. Choose from {list(REGIONS)}")
        self._username = username
        self._password = password
        self._region: RegionConfig = REGIONS[region_key]

        # Auth state
        self._id_token: str | None = None
        self._access_token: str | None = None
        self._identity_id: str | None = None
        self._aws_credentials: dict | None = None

        # Device state
        self._thing_name: str | None = None
        self._state: MowerState = MowerState()
        self._state_lock = threading.Lock()

        # Zone/channel cache (populated from multi-packet map fetch)
        self._zones: list[Zone] = []
        self._channels: list[Channel] = []
        self._map_packets: dict[int, bytes] = {}   # packetIndex → mapData bytes
        self._map_total_packets: int = 0

        # MQTT
        self._mqtt_connection = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._state_listener: Callable | None = None  # called (thread-safe) on state updates
        self._last_map_refresh: float = 0.0  # monotonic time of last QUERY_MAP sent

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _authenticate_sync(self) -> None:
        """Sign in via Cognito SRP and obtain federated AWS credentials."""
        try:
            from pycognito import Cognito
        except ImportError as exc:
            raise LymowApiError("pycognito is required. Install: pip install pycognito") from exc

        try:
            user = Cognito(
                user_pool_id=self._region.user_pool_id,
                client_id=self._region.app_client_id,
                user_pool_region=self._region.aws_region,
                username=self._username,
            )
            user.authenticate(password=self._password)
        except Exception as exc:
            msg = str(exc)
            if "NotAuthorizedException" in msg or "UserNotFoundException" in msg:
                raise LymowAuthError(f"Authentication failed: {msg}") from exc
            raise LymowApiError(f"Cognito error: {msg}") from exc

        self._id_token = user.id_token
        self._access_token = user.access_token

        # Exchange IdToken for federated AWS credentials
        identity_client = boto3.client("cognito-identity", region_name=self._region.aws_region)
        login_key = f"cognito-idp.{self._region.aws_region}.amazonaws.com/{self._region.user_pool_id}"
        logins = {login_key: self._id_token}

        id_resp = identity_client.get_id(
            IdentityPoolId=self._region.identity_pool_id,
            Logins=logins,
        )
        self._identity_id = id_resp["IdentityId"]

        creds_resp = identity_client.get_credentials_for_identity(
            IdentityId=self._identity_id,
            Logins=logins,
        )
        creds = creds_resp["Credentials"]
        # Cognito Identity uses "SecretKey", normalise to the SigV4 convention
        self._aws_credentials = {
            "AccessKeyId": creds["AccessKeyId"],
            "SecretAccessKey": creds.get("SecretAccessKey") or creds["SecretKey"],
            "SessionToken": creds["SessionToken"],
        }
        _LOGGER.debug("Authentication successful, identity: %s", self._identity_id)

    async def authenticate(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._authenticate_sync)

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------

    def _api_headers(self) -> dict[str, str]:
        """Headers for REST API calls — access token auth, no SigV4."""
        return {
            "Authorization": self._access_token or "",
            "Content-Type": "application/json",
        }

    def _get_devices_sync(self) -> list[dict]:
        if not self._identity_id or not self._access_token:
            raise LymowAuthError("Not authenticated")

        import urllib.request

        url = (
            f"{self._region.device_binding_api}/device-list-query"
            f"?p=devices&identityId={self._identity_id}"
        )
        req = urllib.request.Request(url, headers=self._api_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())

        # Response is expected to be a list or dict with a data/devices key
        if isinstance(body, list):
            return body
        return body.get("data", body.get("devices", []))

    async def get_devices(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_devices_sync)

    async def get_zones(self) -> list[Zone]:
        """Return zones from the last received map. Empty until map fetch completes."""
        with self._state_lock:
            return list(self._zones)

    async def get_channels(self) -> list[Channel]:
        """Return channels from the last received map. Empty until map fetch completes."""
        with self._state_lock:
            return list(self._channels)

    # ------------------------------------------------------------------
    # MQTT
    # ------------------------------------------------------------------

    def _on_mqtt_message(self, topic: str, payload: bytes, **_kwargs) -> None:
        try:
            data = decode_pb_output(payload)
        except Exception as exc:
            _LOGGER.warning("Failed to decode PbOutput: %s", exc)
            return

        _ACTIVE = {RobotStatus.CLEANING, RobotStatus.DOCKING, RobotStatus.PAUSE,
                   RobotStatus.PAUSE_DOCKING, RobotStatus.RESUME}
        _DOCKED = {RobotStatus.CHARGING, RobotStatus.CHARGING_FULL, RobotStatus.WAITING}

        with self._state_lock:
            prev = self._state
            new_status = data.get("robot_status")
            new_state = MowerState(
                robot_status=new_status if new_status is not None else prev.robot_status,
                battery=data.get("battery") if data.get("battery") is not None else prev.battery,
                error_codes=data.get("error_codes") if data.get("error_codes") else prev.error_codes,
                warning_codes=data.get("warning_codes") if data.get("warning_codes") else prev.warning_codes,
                position=data.get("position") if data.get("position") is not None else prev.position,
                heading=data.get("heading") if data.get("heading") is not None else prev.heading,
            )
            self._state = new_state
            just_docked = (
                new_status is not None
                and prev.robot_status in _ACTIVE
                and new_state.robot_status in _DOCKED
            )
        _LOGGER.debug("State update: status=%s battery=%s position=%s heading=%s",
                      new_state.status_name, new_state.battery, new_state.position, new_state.heading)

        notify = data.get("position") is not None or just_docked
        if notify and self._state_listener and self._loop:
            self._loop.call_soon_threadsafe(self._state_listener)

        if just_docked:
            _LOGGER.debug("Mower returned to dock — requesting map refresh")
            try:
                self._do_map_refresh_sync()
            except Exception as exc:
                _LOGGER.warning("Map refresh after docking failed: %s", exc)

        bt_map = data.get("bt_map")
        if bt_map and bt_map.get("query_ack"):
            self._handle_map_packet(bt_map["query_ack"])

    def _handle_map_packet(self, query_ack: dict) -> None:
        total = query_ack.get("total_packet", 0)
        index = query_ack.get("packet_index", 0)
        map_data = query_ack.get("map_data", b"")

        if total == 0:
            return

        if total != self._map_total_packets:
            # New map fetch started — reset state
            self._map_packets = {}
            self._map_total_packets = total

        self._map_packets[index] = map_data
        _LOGGER.debug("Map packet %d/%d received (%dB)", index, total - 1, len(map_data))

        # Request next missing packet
        for i in range(total):
            if i not in self._map_packets:
                try:
                    self._publish_sync(encode_get_map_data(i))
                    _LOGGER.debug("Requested map packet %d", i)
                except Exception as exc:
                    _LOGGER.warning("Failed to request map packet %d: %s", i, exc)
                return

        # All packets received — assemble and decode
        full_map_data = b"".join(self._map_packets[i] for i in range(total))
        _LOGGER.debug("Map complete: %dB total", len(full_map_data))
        try:
            map_result = decode_pb_map(full_map_data)
            zones: list[Zone] = []
            for z in map_result.get("go_zones", []):
                zones.append(Zone(
                    name=z["name"],
                    hash_id=z["hash_id"],
                    zone_type="go",
                    polygon=z.get("polygon", []),
                ))
            for z in map_result.get("nogo_zones", []):
                zones.append(Zone(
                    name=z["name"],
                    hash_id=z["hash_id"],
                    zone_type="nogo",
                    polygon=z.get("polygon", []),
                ))
            channels = [
                Channel(points=ch["points"])
                for ch in map_result.get("channels", [])
            ]
            with self._state_lock:
                self._zones = zones
                self._channels = channels
            _LOGGER.info("Map decoded: %d go zones, %d nogo zones, %d channels",
                         len(map_result.get("go_zones", [])), len(map_result.get("nogo_zones", [])),
                         len(channels))
        except Exception as exc:
            _LOGGER.warning("Failed to decode map: %s", exc)

    def _connect_sync(self) -> None:
        if not self._aws_credentials:
            raise LymowAuthError("Not authenticated")

        try:
            from awscrt.auth import AwsCredentialsProvider
            from awsiot import mqtt_connection_builder
        except ImportError as exc:
            raise LymowConnectionError(
                "awsiotsdk is required for MQTT. Install: pip install awsiotsdk"
            ) from exc

        creds = self._aws_credentials
        credentials_provider = AwsCredentialsProvider.new_static(
            access_key_id=creds["AccessKeyId"],
            secret_access_key=creds["SecretAccessKey"],
            session_token=creds["SessionToken"],
        )

        self._mqtt_connection = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=self._region.iot_endpoint,
            region=self._region.aws_region,
            credentials_provider=credentials_provider,
            client_id=f"lymow-ha-{uuid.uuid4()}",
            clean_session=True,
            keep_alive_secs=30,
        )

        connect_future = self._mqtt_connection.connect()
        connect_future.result(timeout=15)
        _LOGGER.debug("MQTT connected to %s", self._region.iot_endpoint)

        if self._thing_name:
            self._subscribe_sync()

    def _on_notify_message(self, topic: str, payload: bytes, **_kwargs) -> None:
        """Log everything received on notify-app for protocol discovery."""
        raw = payload if isinstance(payload, bytes) else payload.encode()
        _LOGGER.warning(
            "notify-app message (%dB) hex=%s text=%r",
            len(raw), raw.hex(), raw[:200],
        )
        # Also attempt protobuf decode in case it shares the PbOutput schema
        try:
            data = decode_pb_output(raw)
            _LOGGER.warning("notify-app decoded as PbOutput: %s", data)
        except Exception:
            pass

    def _subscribe_sync(self) -> None:
        if not self._mqtt_connection or not self._thing_name:
            return
        from awscrt import mqtt as awsmqtt

        topic = f"/device/{self._thing_name}/pboutput"
        sub_future, _ = self._mqtt_connection.subscribe(
            topic=topic,
            qos=awsmqtt.QoS.AT_LEAST_ONCE,
            callback=self._on_mqtt_message,
        )
        sub_future.result(timeout=10)
        _LOGGER.debug("Subscribed to %s", topic)

        notify_topic = f"/device/{self._thing_name}/notify-app"
        sub_future2, _ = self._mqtt_connection.subscribe(
            topic=notify_topic,
            qos=awsmqtt.QoS.AT_LEAST_ONCE,
            callback=self._on_notify_message,
        )
        sub_future2.result(timeout=10)
        _LOGGER.debug("Subscribed to %s", notify_topic)

        # Request map and initial state
        self._publish_sync(encode_query_map())
        self._last_map_refresh = time.monotonic()

    def _publish_sync(self, payload: str) -> None:
        """Publish a base64-encoded protobuf payload to the device.

        The app wraps payloads in JSON: {"message":"<base64_proto>"}
        This matches what the real Lymow app sends (confirmed via logcat MITM).
        """
        if not self._mqtt_connection or not self._thing_name:
            raise LymowConnectionError("Not connected or no device bound")
        import json as _json
        from awscrt import mqtt as awsmqtt

        topic = f"/device/{self._thing_name}/pbinput"
        mqtt_payload = _json.dumps({"message": payload})
        try:
            pub_future, _ = self._mqtt_connection.publish(
                topic=topic,
                payload=mqtt_payload,
                qos=awsmqtt.QoS.AT_LEAST_ONCE,
            )
            pub_future.result(timeout=10)
        except Exception as exc:
            # Mark connection as dead so reconnect is attempted on next call
            self._mqtt_connection = None
            raise LymowConnectionError(f"MQTT publish failed: {exc}") from exc

    async def connect(self, thing_name: str) -> None:
        self._thing_name = thing_name
        self._loop = asyncio.get_event_loop()
        await self._loop.run_in_executor(None, self._connect_sync)

    async def reconnect(self) -> None:
        """Re-authenticate and reconnect MQTT. Called when connection drops."""
        _LOGGER.warning("MQTT connection lost — re-authenticating and reconnecting")
        try:
            loop = asyncio.get_event_loop()
            if self._mqtt_connection:
                try:
                    await loop.run_in_executor(
                        None, lambda: self._mqtt_connection.disconnect().result(timeout=5)
                    )
                except Exception:
                    pass
                self._mqtt_connection = None
            await self.authenticate()
            await loop.run_in_executor(None, self._connect_sync)
            _LOGGER.warning("MQTT reconnected successfully")
        except Exception as exc:
            raise LymowConnectionError(f"Reconnect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._mqtt_connection:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: self._mqtt_connection.disconnect().result(timeout=5)
            )
            self._mqtt_connection = None

    async def _publish(self, payload: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._publish_sync, payload)

    def _publish_nowait(self, payload: str) -> None:
        """Publish without waiting for PUBACK — for commands where we don't need confirmation."""
        if not self._mqtt_connection or not self._thing_name:
            raise LymowConnectionError("Not connected or no device bound")
        import json as _json
        from awscrt import mqtt as awsmqtt

        topic = f"/device/{self._thing_name}/pbinput"
        mqtt_payload = _json.dumps({"message": payload})
        self._mqtt_connection.publish(
            topic=topic,
            payload=mqtt_payload,
            qos=awsmqtt.QoS.AT_LEAST_ONCE,
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    _MAP_REFRESH_INTERVAL = 3600  # hourly fallback

    async def request_map_refresh(self) -> None:
        """Send QUERY_MAP if the hourly fallback interval has elapsed."""
        if not self._mqtt_connection or not self._thing_name:
            return
        if time.monotonic() - self._last_map_refresh >= self._MAP_REFRESH_INTERVAL:
            await self._publish(encode_query_map())
            self._last_map_refresh = time.monotonic()

    def _do_map_refresh_sync(self) -> None:
        """Synchronous map refresh — called from MQTT thread after docking."""
        self._publish_sync(encode_query_map())
        self._last_map_refresh = time.monotonic()

    async def get_state(self) -> MowerState:
        with self._state_lock:
            return MowerState(
                robot_status=self._state.robot_status,
                battery=self._state.battery,
                error_codes=list(self._state.error_codes),
                warning_codes=list(self._state.warning_codes),
                position=self._state.position,
                heading=self._state.heading,
            )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def start_mowing(self, zone_hash_id: str | None = None) -> None:
        if zone_hash_id:
            await self._publish(encode_start_zone([zone_hash_id]))
        else:
            await self._publish(encode_command(UserCtrl.CLEAN))

    async def start_mowing_zones(self, zone_hash_ids: list[str]) -> None:
        await self._publish(encode_start_zone(zone_hash_ids))

    async def stop_mowing(self) -> None:
        await self._publish(encode_command(UserCtrl.PAUSE))

    async def resume_mowing(self) -> None:
        await self._publish(encode_command(UserCtrl.RESUME))

    async def cancel_task(self) -> None:
        await self._publish(encode_command(UserCtrl.FORCE_REINIT))

    async def return_to_dock(self) -> None:
        await self._publish(encode_command(UserCtrl.RECHARGE_DOCK))

    async def clear_error(self) -> None:
        # The app sends PAUSE (not RESUME) to acknowledge an error and return
        # the mower to a stable paused/waiting state.
        await self._publish(encode_command(UserCtrl.PAUSE))
