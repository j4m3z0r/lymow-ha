# Lymow Cloud API — Protocol Reference

## Architecture Overview

```
Client (app/HA) → AWS Cognito → AWS Credentials
                                     ↓
                         AWS IoT Core (MQTT over WSS)
                              ↓              ↑
                     publish commands    subscribe status
                         ↓                    ↑
                     /device/{thingName}/pbinput
                     /device/{thingName}/pboutput
                     /device/{thingName}/notify-app
```

- **Auth**: AWS Cognito User Pools (per-region)
- **Real-time control/status**: AWS IoT Core MQTT over WebSocket
- **Messages**: Protocol Buffers (`PbInput` / `PbOutput`), base64-encoded on the wire
- **Device management**: AWS API Gateway REST APIs (Cognito JWT auth)

---

## 1. Authentication — AWS Cognito

The app selects a region based on the user's account region at signup.
Each region has its own Cognito User Pool, Identity Pool, and API endpoints.

| Region key | AWS Region    | User Pool ID                   | App Client ID                    |
|------------|---------------|--------------------------------|----------------------------------|
| `ap`       | ap-southeast-2| `ap-southeast-2_vNriuUNeQ`     | `2ch3nqqr0usf5sadvcrj2hp6ll`     |
| `eu`       | eu-west-1     | `eu-west-1_6qNPbnrrd`          | `3h1sqv3hishjiofbv8giskjgb0`     |
| `us`       | us-east-2     | `us-east-2_GAyiLkZQf`          | `3ftv5jumkv375hic8dpdqodj8n`     |
| `cn`       | ap-east-1     | `ap-east-1_23Lf1WZer`          | `46mirppdlu6mrbjd5bkiil0n20`     |

| Region key | Identity Pool ID                                        |
|------------|---------------------------------------------------------|
| `ap`       | `ap-southeast-2:87d0fe24-16af-4189-b02f-984a7ed14ee0`  |
| `eu`       | `eu-west-1:c905a69c-0153-401a-a879-0c50b892015b`       |
| `us`       | `us-east-2:037db699-5df0-4ed2-92b8-0dd0f1843918`       |
| `cn`       | `ap-east-1:3e9265aa-f564-4083-8e1e-988e6cfdc446`       |

### Auth flow

1. **Sign in** via Cognito USER_SRP_AUTH using the region-specific User Pool + App Client ID.
   Returns: `AccessToken`, `IdToken`, `RefreshToken`.

2. **Federated credentials** — exchange the `IdToken` with the Cognito
   Identity Pool to get temporary AWS credentials (`AccessKeyId`,
   `SecretAccessKey`, `SessionToken`). These are required for:
   - Signing the MQTT WebSocket connection (SigV4)
   - Signing REST API Gateway requests

3. **Token refresh** — use `RefreshToken` with Cognito to get new
   `AccessToken`/`IdToken` when they expire (~1h).

---

## 2. REST APIs — Device Management

All API Gateway endpoints use a Cognito User Pool authorizer — pass the
**Cognito access token** (not the ID token, not SigV4) directly as the
`Authorization` header value (no `Bearer` prefix).

```
Authorization: <accessToken>
Content-Type: application/json
```

### Endpoints by region

| API name           | ap-southeast-2 base URL                                         |
|--------------------|-----------------------------------------------------------------|
| `deviceBindingApi` | `https://1sfa49lnl8.execute-api.ap-southeast-2.amazonaws.com/prod` |
| `deviceProfileApi` | `https://7k2iuc99h7.execute-api.ap-southeast-2.amazonaws.com/prod` |
| `userAccountApi`   | `https://l2gobpcoqc.execute-api.ap-southeast-2.amazonaws.com/prod` |
| `notificationApi`  | `https://inflizu44a.execute-api.ap-southeast-2.amazonaws.com/prod` |
| `checkUpdateApi`   | `https://v7tlj1gnw7.execute-api.ap-southeast-2.amazonaws.com/prod` |
| `s3Api`            | `https://2xipi98nw3.execute-api.ap-southeast-2.amazonaws.com/prod` |
| `kvsApi`           | `https://vvikmtssjh.execute-api.ap-southeast-2.amazonaws.com/prod` (Kinesis Video) |

### Key REST paths

| Method | API              | Path                                              | Purpose                        |
|--------|------------------|---------------------------------------------------|--------------------------------|
| GET    | deviceBindingApi | `/device-list-query?p=devices&identityId={id}`    | List bound devices             |
| POST   | deviceBindingApi | `/device-activation`                              | Bind/activate a new device     |
| POST   | deviceBindingApi | `/device-unbinding`                               | Unbind device                  |
| POST   | deviceBindingApi | `/device-update`                                  | Update device name/info        |
| GET    | deviceProfileApi | `/get-device-info`                                | Get device info/status         |
| GET    | deviceProfileApi | `/get-device-feature`                             | Get device feature flags       |
| POST   | deviceProfileApi | `/update-device-feature`                          | Update device features         |
| POST   | userAccountApi   | `/update-user-profile`                            | Update user profile            |

The device list response includes the `thingName` for each device, which is
required for MQTT topic construction.

---

## 3. MQTT — Real-time Control and Status

### Broker

`a3j5zqqo5iuph9-ats.iot.{aws-region}.amazonaws.com:443` (WSS)

Connection requires SigV4-signed WebSocket URL using the federated AWS
credentials from step 2 of the auth flow.

### Topics

| Direction        | Topic                              | Payload                  |
|------------------|------------------------------------|--------------------------|
| App → Device     | `/device/{thingName}/pbinput`      | PbInput (protobuf+base64)|
| Device → App     | `/device/{thingName}/pboutput`     | PbOutput (protobuf+base64)|
| Device → App     | `/device/{thingName}/notify-app`   | Notifications            |

### Message encoding

1. Build a `PbInput` protobuf message (fields described below)
2. Encode to binary with `PbInput.encode().finish()`
3. Base64-encode the result
4. Publish the base64 string to the MQTT topic

Incoming `PbOutput` messages are base64-decoded then protobuf-decoded.

---

## 4. Protobuf Message Schemas

### PbInput (commands — app → device)

```protobuf
message PbInput {
  UserCtrl userCtrl = 1;     // command enum
  int32 version = 2;         // protocol version (49 = PB_VERSION_4_9)
  PbMap map = 3;             // zone info for mow commands
  PbRobotConfig robotConfig = 4;
  // ... other fields for BLE/setup flows
}
```

### PbOutput (status — device → app)

Key fields in `PbOutput`:
```protobuf
message PbOutput {
  PbDeviceInfo deviceInfo = 1;     // battery, work status
  PbCleanInfo cleanInfo = 2;       // clean progress
  PbMap map = 3;                   // live map data
  PbPath path = 4;                 // mowing path
  PbRobotConfig robotConfig = 5;   // current robot config
  repeated int32 errorCodes = 6;   // active error codes
  repeated int32 warningCodes = 7; // active warning codes
  PbTaskConfig taskConfig = 8;
  PbSchedule schedule = 9;
  PbRobotInfo robotInfo = 10;
  // ... many other fields
}
```

### UserCtrl enum (commands)

| Value | Name                              | Notes                           |
|-------|-----------------------------------|---------------------------------|
| 0     | `USER_CTRL_NONE`                  | No-op                           |
| 1     | `USER_CTRL_CLEAN`                 | Start mowing all zones          |
| 2     | `USER_CTRL_DOCK`                  | Return to dock immediately      |
| 3     | `USER_CTRL_PAUSE`                 | Pause / acknowledge error       |
| 4     | `USER_CTRL_RESUME`                | Resume mowing                   |
| 5     | `USER_CTRL_GO_ZONE_PARTITION`     | Mow a specific zone partition   |
| 6     | `USER_CTRL_NO_GO_ZONE_PARTITION`  | No-go zone partition            |
| 7     | `USER_CTRL_EXIT_ZONE_PARTITION`   | Exit zone partition mode        |
| 8     | `USER_CTRL_CLEAR_ZONE`            | Clear a zone                    |
| 18    | `USER_CTRL_LOCK`                  | Lock robot                      |
| 19    | `USER_CTRL_QUERY_MAP`             | Request current map             |
| 20    | `USER_CTRL_QUERY_SCHEDULES`       | Request schedules               |
| 21    | `USER_CTRL_PAUSE_DOCK`            | Pause + return to dock          |
| 22    | `USER_CTRL_RESUME_DOCK`           | Resume docking after pause      |
| 28    | `USER_CTRL_FORCE_REINIT`          | Cancel current task             |
| 33    | `USER_CTRL_RECHARGE_DOCK`         | **Return to dock to recharge**  |
| 36    | `USER_CTRL_SET_TASK_CONFIG`       | Set task configuration          |
| 46    | `USER_CTRL_START_MOW_SCHEDULE`    | Start a scheduled mow           |

For **start mowing all zones**: send `USER_CTRL_CLEAN` with no map payload.

For **mow selected zones**: send `USER_CTRL_CLEAN` with a `PbMap` containing
the selected `goZones` (each with `PbZoneBasicInfo.hashId` identifying the zone).

For **return to dock**: send `USER_CTRL_RECHARGE_DOCK`.

For **pause**: send `USER_CTRL_PAUSE`.

For **acknowledge / clear error**: send `USER_CTRL_PAUSE`.

### RobotStatus enum (status values in PbOutput)

| Value | Name                          | Notes                     |
|-------|-------------------------------|---------------------------|
| 0     | `ROBOT_STATUS_NONE`           | Unknown / initializing    |
| 1     | `ROBOT_STATUS_WAITING`        | Idle / waiting            |
| 2     | `ROBOT_STATUS_CLEANING`       | **Actively mowing**       |
| 3     | `ROBOT_STATUS_PAUSE`          | **Paused**                |
| 4     | `ROBOT_STATUS_DOCKING`        | **Returning to dock**     |
| 5     | `ROBOT_STATUS_CHARGING`       | Charging                  |
| 6     | `ROBOT_STATUS_REMOTE_CONTROL` | Remote control mode       |
| 7     | `ROBOT_STATUS_ERROR`          | **Error state**           |
| 8     | `ROBOT_STATUS_RESUME`         | Resuming                  |
| 9     | `ROBOT_STATUS_ZONE_PARTITION` | Zone mapping mode         |
| 10    | `ROBOT_STATUS_PAUSE_DOCKING`  | **Paused, returning to dock** |
| 11    | `ROBOT_STATUS_UPDATING`       | Firmware update           |
| 12    | `ROBOT_STATUS_CHARGING_FULL`  | Fully charged             |
| 13    | `ROBOT_STATUS_EMERGENCY_STOP` | Emergency stop            |

---

## 5. Zone Identification

Each zone has a `hashId` (string) used in `PbZoneBasicInfo`. The map of
zones is retrieved via `USER_CTRL_QUERY_MAP` command — the device responds
with a `PbOutput` containing the full `PbMap` with all zone definitions.

---

## 6. Implementation Notes

### Recommended approach

1. Use `boto3` (Cognito IDP + Cognito Identity) for auth and credential management.
2. For MQTT: use `awsiotsdk` (AWS IoT Device SDK for Python v2) which handles
   SigV4-signed WebSocket connections natively.
3. For protobuf: use the hand-written encoder/decoder in `lymow_api/proto.py`
   (field numbers were validated against live device traffic).

### REST-only polling (alternative)

AWS IoT Core exposes a REST endpoint for publishing MQTT messages:
```
POST https://{iotEndpoint}/topics/{url-encoded-topic}?qos=0
Authorization: AWS SigV4
Body: base64-encoded protobuf
```

This avoids a persistent MQTT connection. **Note:** `GET /get-device-info` does NOT
include live robot status (battery, mowing state) — it only has device metadata.
Live status requires MQTT.

### Verified response shapes

**`GET /device-list-query?p=devices&identityId={id}`** — returns array:
```json
[{
  "deviceThingName": "device_xxxxxxxxxxxx",
  "deviceType": "Lymow one",
  "deviceName": "My Mower",
  "deviceBluetooth": "Lymow_XXXX",
  "createdAt": "2026-03-07T18:00:25.186Z",
  "deviceState": "online",
  "deviceLocked": false,
  "sn": "LR0XXXXXXXXXX"
}]
```

**`GET /get-device-info?deviceThingName={thingName}`** — returns:
```json
{
  "deviceThingName": "device_xxxxxxxxxxxx",
  "robotLocation": [],
  "deviceState": "online",
  "softwareVersion": "v2.1.44",
  "mcuVersion": "app2.3.9 bl0.0.1",
  "macAddress": "XX:XX:XX:XX:XX:XX",
  "cleanSchedules": ""
}
```
No battery or mowing status fields — live status comes via MQTT only.

---

## 7. S3 and Kinesis Video (out of scope for v1)

- `s3Api`: user data storage (maps, logs) in `lymow-user-data-{region}`
- `kvsApi`: Kinesis Video Streams — used for the live camera feed
