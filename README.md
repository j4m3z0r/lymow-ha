# lymow-ha

Home Assistant custom integration for the [Lymow](https://lymow.com/) robot mower.

Controls and monitors a Lymow mower via the same cloud API used by the official mobile app (AWS IoT Core MQTT + Cognito auth).

## Features

- **Live status** — mowing, docked, charging, error, etc.
- **Battery level** sensor
- **Map display** — Lovelace card showing zone polygons, no-go zones, and the mower's live position with heading arrow
- **Zone control** — select and start mowing individual zones by name
- **Commands** — start mowing, pause, resume, return to dock, clear error, cancel task
- **Real-time updates** — position and status refresh instantly as the device sends heartbeats (no polling lag)

## Requirements

- Home Assistant 2024.1+
- A Lymow account and a paired mower
- Python packages installed automatically by HA: `pycognito`, `boto3`, `awsiotsdk`

## Installation

### Via HACS (recommended)

1. In HACS, go to the overflow menu (⋮) and choose **Custom repositories**
2. Add `https://github.com/j4m3z0r/lymow-ha` with category **Integration**
3. Search for **Lymow Robot Mower** and click **Download**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** and search for **Lymow**

### Manual

1. Download or clone this repository
2. Copy the integration folder into your HA config directory:

   ```bash
   cp -r custom_components/lymow /config/custom_components/lymow
   ```

3. Copy the Lovelace map card:

   ```bash
   mkdir -p /config/www
   cp www/lymow-map-card.js /config/www/lymow-map-card.js
   ```

4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** and search for **Lymow**

## Configuration

Enter your Lymow app **email** and **password**, then select your account **region** (ap / eu / us / cn). The integration discovers your mower automatically.

If you are unsure of your region, leave it on **ap** — the integration will try all regions and use whichever one finds your account.

## Lovelace Map Card

The map card displays zone polygons (green = mowing zones, red = no-go zones), channel lines, and a directional arrow for the mower's live position.

### 1. Register the resource

Go to **Settings → Dashboards → Resources** (or edit `/config/.storage/lovelace_resources`) and add:

| URL | Type |
|-----|------|
| `/local/lymow-map-card.js` | JavaScript module |

### 2. Add the card

```yaml
type: custom:lymow-map-card
entity: sensor.lymow_mower_map
```

Dark mode is supported automatically.

## Service Actions

All services accept an optional `entity_id` to target a specific mower. If omitted, the action applies to all configured mowers.

### `lymow.start_mowing`

Start mowing one or more specific zones by name or hash ID.

```yaml
action: lymow.start_mowing
data:
  zones:
    - "Front Lawn"
    - "Back Yard"
```

Zone names are matched case-insensitively. Curly/smart apostrophes are normalised automatically. If no zones are specified, the mower uses its default mowing plan.

### `lymow.resume_mowing`

Resume mowing after a pause.

```yaml
action: lymow.resume_mowing
```

### `lymow.clear_error`

Acknowledge and clear an error state, returning the mower to a paused/ready state. After clearing, use `resume_mowing` or press **Start** to begin mowing again.

```yaml
action: lymow.clear_error
```

### `lymow.cancel_task`

Cancel the current mowing task and return the mower to a waiting/idle state.

```yaml
action: lymow.cancel_task
```

### `lymow.dock`

Send the mower back to its charging dock.

```yaml
action: lymow.dock
```

## How It Works

The Lymow app communicates with the mower via AWS IoT Core MQTT using a custom Protocol Buffers schema. This integration:

1. Authenticates with AWS Cognito (SRP auth) to obtain temporary AWS credentials
2. Opens a SigV4-signed WebSocket MQTT connection to AWS IoT Core
3. Subscribes to the device's output topic (`/device/{thingName}/pboutput`)
4. Sends protobuf-encoded commands to the input topic (`/device/{thingName}/pbinput`)
5. Decodes map data from multi-packet responses to the `QUERY_MAP` command

See [`docs/api.md`](docs/api.md) for the protocol reference.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pycognito boto3 awsiotsdk pytest

# Run tests
pytest tests/

# Deploy to a local HA instance (SSH required)
./scripts/deploy.sh

# Probe script — connects to the real device and prints live MQTT messages
export LYMOW_USER=your@email.com LYMOW_PASS=yourpassword LYMOW_REGION=us
python scripts/probe.py
```

## Licence

MIT
