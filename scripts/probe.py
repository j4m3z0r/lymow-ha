#!/usr/bin/env python3
"""Developer probe: authenticate, list devices, and query live status via MQTT."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "custom_components", "lymow"))

_TOKEN_CACHE = os.path.join(os.path.dirname(__file__), ".probe_tokens.json")


def _load_cached_tokens():
    """Load cached tokens if they exist and aren't expired."""
    try:
        with open(_TOKEN_CACHE) as f:
            data = json.load(f)
        expiry = data.get("expiry", 0)
        if time.time() < expiry - 60:
            return data
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None


def _save_tokens(client, region_key: str):
    """Save tokens to cache file."""
    creds = client._aws_credentials
    expiry_raw = creds.get("Expiration")
    if expiry_raw and hasattr(expiry_raw, "timestamp"):
        expiry = expiry_raw.timestamp()
    else:
        expiry = time.time() + 3000
    data = {
        "region_key": region_key,
        "identity_id": client._identity_id,
        "id_token": client._id_token,
        "access_token": client._access_token,
        "aws_credentials": {k: str(v) if not isinstance(v, str) else v for k, v in creds.items()},
        "expiry": expiry,
    }
    with open(_TOKEN_CACHE, "w") as f:
        json.dump(data, f, indent=2)


async def main():
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    region_key = os.environ.get("LYMOW_REGION", "ap")

    if not username or not password:
        print("Set LYMOW_USER and LYMOW_PASS environment variables.")
        sys.exit(1)

    from lymow_api.client import LymowClient
    from lymow_api.regions import REGIONS

    region = REGIONS[region_key]
    client = LymowClient(username, password, region_key=region_key)

    # ------------------------------------------------------------------ Auth
    print("\n=== Step 1: Authenticate ===")
    cached = _load_cached_tokens()
    if cached and cached.get("region_key") == region_key:
        print("  Using cached tokens (skipping auth).")
        client._identity_id = cached["identity_id"]
        client._id_token = cached["id_token"]
        client._access_token = cached["access_token"]
        client._aws_credentials = cached["aws_credentials"]
    else:
        await client.authenticate()
        _save_tokens(client, region_key)
        print("  Authenticated and cached tokens.")
    print(f"  identity_id : {client._identity_id}")
    print(f"  aws_ak      : {client._aws_credentials['AccessKeyId']}")

    # ------------------------------------------------------------------ Device list
    print("\n=== Step 2: Device list ===")
    import urllib.request

    url = (
        f"{region.device_binding_api}/device-list-query"
        f"?p=devices&identityId={client._identity_id}"
    )
    headers = client._api_headers()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        body = json.loads(raw)
        print("  Response (pretty):")
        print(json.dumps(body, indent=2, default=str))
    except Exception as exc:
        print(f"  ERROR: {exc}")
        body = {}

    devices = body if isinstance(body, list) else body.get("data", body.get("devices", []))
    thing_name = None
    if devices:
        d0 = devices[0] if isinstance(devices, list) else devices
        thing_name = (
            d0.get("deviceThingName")
            or d0.get("thingName")
            or d0.get("thing_name")
        )
        print(f"\n  thing_name  : {thing_name}")

    # ------------------------------------------------------------------ get-device-info
    print("\n=== Step 3: get-device-info ===")
    params = f"?deviceThingName={thing_name}" if thing_name else ""
    url2 = f"{region.device_profile_api}/get-device-info{params}"
    req2 = urllib.request.Request(url2, headers=client._api_headers())
    try:
        with urllib.request.urlopen(req2, timeout=15) as resp2:
            raw2 = resp2.read()
        print("  Response (pretty):")
        print(json.dumps(json.loads(raw2), indent=2, default=str))
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ------------------------------------------------------------------ get-device-feature
    print("\n=== Step 4: get-device-feature ===")
    params3 = f"?deviceThingName={thing_name}" if thing_name else ""
    url3 = f"{region.device_profile_api}/get-device-feature{params3}"
    req3 = urllib.request.Request(url3, headers=client._api_headers())
    try:
        with urllib.request.urlopen(url3, timeout=15) as resp3:
            raw3 = resp3.read()
        print("  Response (pretty):")
        print(json.dumps(json.loads(raw3), indent=2, default=str))
    except Exception as exc:
        print(f"  ERROR: {exc}")

    # ------------------------------------------------------------------ IoT endpoint
    print("\n=== Step 4b: IoT endpoint ===")
    import boto3
    creds_now = client._aws_credentials
    iot_client = boto3.client(
        "iot",
        region_name=region.aws_region,
        aws_access_key_id=creds_now["AccessKeyId"],
        aws_secret_access_key=creds_now["SecretAccessKey"],
        aws_session_token=creds_now.get("SessionToken"),
    )
    try:
        ep = iot_client.describe_endpoint(endpointType="iot:Data-ATS")
        actual_iot_endpoint = ep["endpointAddress"]
        print(f"  Actual IoT endpoint : {actual_iot_endpoint}")
        print(f"  Config IoT endpoint : {region.iot_endpoint}")
    except Exception as exc:
        print(f"  ERROR: {exc}")
        actual_iot_endpoint = region.iot_endpoint

    # ------------------------------------------------------------------ MQTT subscribe + QUERY_MAP
    if not thing_name:
        print("\nNo thing_name found — skipping MQTT step.")
        return

    print(f"\n=== Step 5: MQTT — wake-up ping then QUERY_MAP ===")
    try:
        from awscrt.auth import AwsCredentialsProvider
        from awsiot import mqtt_connection_builder
        from awscrt import mqtt as awsmqtt
        import uuid
    except ImportError:
        print("  awsiotsdk not installed. Install: pip install awsiotsdk")
        return

    received: list[dict] = []

    def on_message(topic, payload, **_):
        from lymow_api.proto import decode_pb_output, decode_pb_map
        raw = payload if isinstance(payload, bytes) else payload.encode()
        is_notify = "notify-app" in topic
        prefix = "NOTIFY" if is_notify else "MSG"
        print(f"\n  {prefix} on {topic!r} ({len(raw)}B)  hex={raw.hex()}")
        try:
            data = decode_pb_output(raw)
            received.append(data)
            bt = data.get("bt_map")
            if bt and bt.get("query_ack") and bt["query_ack"]["total_packet"] > 0:
                ack = bt["query_ack"]
                map_result = decode_pb_map(ack["map_data"])
                data["_map_decoded"] = map_result
                print(f"    -> btMap: totalPacket={ack['total_packet']} dataLen={len(ack['map_data'])} "
                      f"go_zones={len(map_result['go_zones'])} nogo_zones={len(map_result['nogo_zones'])}")
            else:
                print(f"    -> status={data.get('robot_status')} battery={data.get('battery')} "
                      f"position={data.get('position')} errors={data.get('error_codes')}")
        except Exception as exc:
            print(f"    -> decode failed: {exc}")

    creds = client._aws_credentials
    credentials_provider = AwsCredentialsProvider.new_static(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
    )

    conn = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=actual_iot_endpoint,
        region=region.aws_region,
        credentials_provider=credentials_provider,
        client_id=f"lymow-probe-{uuid.uuid4()}",
        clean_session=True,
        keep_alive_secs=30,
    )

    print("  Connecting...")
    conn.connect().result(timeout=15)
    print("  Connected.")

    topic_out = f"/device/{thing_name}/pboutput"
    topic_notify = f"/device/{thing_name}/notify-app"

    sub_future, _ = conn.subscribe(topic=topic_out, qos=awsmqtt.QoS.AT_LEAST_ONCE, callback=on_message)
    sub_future.result(timeout=10)
    print(f"  Subscribed to {topic_out}")
    sub_future2, _ = conn.subscribe(topic=topic_notify, qos=awsmqtt.QoS.AT_LEAST_ONCE, callback=on_message)
    sub_future2.result(timeout=10)
    print(f"  Subscribed to {topic_notify}")

    print("  Waiting 5s for unprompted messages...")
    time.sleep(5)

    from lymow_api.proto import encode_query_map, _field_varint, UserCtrl, PB_VERSION

    topic_in = f"/device/{thing_name}/pbinput"

    def _pub(label, payload_bytes_or_b64, timeout=10):
        if isinstance(payload_bytes_or_b64, bytes):
            b64 = base64.b64encode(payload_bytes_or_b64).decode()
            raw = payload_bytes_or_b64
        else:
            b64 = payload_bytes_or_b64
            raw = base64.b64decode(b64)
        print(f"  [{label}] hex={raw.hex()!r}")
        try:
            mqtt_payload = json.dumps({"message": b64})
            conn.publish(topic=topic_in, payload=mqtt_payload, qos=awsmqtt.QoS.AT_LEAST_ONCE)[0].result(timeout=timeout)
            print(f"    Published OK.")
            return True
        except Exception as exc:
            print(f"    Publish FAILED: {exc}")
            return False

    wake_msg = _field_varint(5, UserCtrl.QUERY_SCHEDULES)
    wake_msg += _field_varint(2, PB_VERSION)
    wake_msg += _field_varint(7, 2)  # appConnect = TOGGLE_CONNECTED
    print("\n  Sending wake-up (QUERY_SCHEDULES + appConnect=CONNECTED)...")
    _pub("wake-up", wake_msg)
    print("  Waiting 5s...")
    time.sleep(5)

    print("\n  Sending QUERY_MAP...")
    _pub("QUERY_MAP", encode_query_map())
    print("  Waiting 20s for map response...")
    time.sleep(20)

    print("  Sending QUERY_MAP again...")
    _pub("QUERY_MAP #2", encode_query_map())
    print("  Waiting 15s...")
    time.sleep(15)

    map_responses = [r for r in received if r.get("_map_decoded")]
    go_zones = []
    if map_responses:
        m = map_responses[-1]["_map_decoded"]
        go_zones = [z for z in m["go_zones"] if z["name"]]
        print(f"\n  Named go zones ({len(go_zones)}):")
        for z in go_zones:
            print(f"    name={z['name']!r}  hash_id={z['hash_id']!r}")
        print(f"\n  All go zones: {len(m['go_zones'])}, nogo zones: {len(m['nogo_zones'])}")
    elif received:
        print("\n  No map in responses. Last message:")
        print(json.dumps({k: v for k, v in received[-1].items() if k != "_map_decoded"}, indent=2, default=str))

    # ------------------------------------------------------------------ Optional: test start_zone
    test_zone_name = os.environ.get("LYMOW_TEST_ZONE")
    if not test_zone_name:
        print("\n=== Step 6: start_zone test (skipped — set LYMOW_TEST_ZONE=<name> to enable) ===")
        conn.disconnect().result(timeout=5)
        print("  Disconnected.")
        return

    print(f"\n=== Step 6: start_zone test (zone={test_zone_name!r}) ===")
    def _norm(s): return s.replace("\u2019", "'").replace("\u2018", "'").casefold()
    target = next((z for z in go_zones if _norm(z["name"]) == _norm(test_zone_name)), None)
    if not target:
        print(f"  Zone {test_zone_name!r} not found. Available: {[z['name'] for z in go_zones]}")
        conn.disconnect().result(timeout=5)
        return

    from lymow_api.proto import encode_start_zone
    payload = encode_start_zone([target["hash_id"]])
    raw_bytes = base64.b64decode(payload)
    print(f"  hash_id : {target['hash_id']!r}")
    print(f"  payload : {raw_bytes.hex()}")
    print(f"  WARNING: This will command the mower to start mowing {test_zone_name!r}!")
    print(f"  Sending in 3 seconds... press Ctrl+C to abort.")
    time.sleep(3)

    ok = _pub(f"start_zone({test_zone_name})", payload, timeout=15)
    if ok:
        print("  PUBACK received — message reached AWS IoT broker.")
        print("  Waiting 10s to see if mower responds...")
        time.sleep(10)

    conn.disconnect().result(timeout=5)
    print("  Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
