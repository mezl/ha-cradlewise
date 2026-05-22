# Cradlewise Smart Crib for Home Assistant

Home Assistant integration for the [Cradlewise Smart Crib](https://www.cradlewise.com/) using the unofficial API.

> **Note:** This uses an unofficial API. Cradlewise may change or restrict access at any time.

## Features

### Sensors & Binary Sensors (cloud, all cribs)

| Entity | Type | Description |
|--------|------|-------------|
| Sleep phase | Sensor | Current sleep state (away / awake / stirring / sleep) |
| Baby present | Binary sensor | Baby detected in crib |
| Bouncing | Binary sensor | Rocking motor active |
| Music playing | Binary sensor | White noise / music active |
| Night light | Binary sensor | Nightlight active |
| Soothe count | Sensor | Soothing interventions today |
| Total sleep | Sensor | Total sleep time today |

Real-time updates via cloud MQTT when available, with REST polling fallback.

### Live Camera (local network, optional)

| Entity | Type | Description |
|--------|------|-------------|
| Camera | Camera | Live 1280×720 H.264 stream direct from the crib |

Video is streamed directly over your LAN via WebRTC — no cloud round-trip.
This requires the crib to be on the same local network as Home Assistant
and a one-time cert extraction step (see [Camera Setup](#camera-setup-optional) below).

## Installation

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mezl&repository=ha-cradlewise&category=integration)

Or manually: copy `custom_components/cradlewise` to your HA `config/custom_components/` directory.

## Setup

1. Restart Home Assistant
2. Go to **Settings → Devices & Services → Add Integration**
3. Search for **Cradlewise**
4. Enter your Cradlewise account email and password
5. *(Optional)* Enter your crib's LAN IP address to enable live video

## Camera Setup (optional)

The camera entity streams H.264 video directly from the crib's local Ant Media Server
over WebRTC. It requires mutual TLS certificates that are bundled in the Cradlewise app.

### 1. Extract the certificates

```bash
# Decompile the Cradlewise APK (install apktool first)
apktool d cradlewise.apk -o cradlewise_unpacked

# Copy the three cert files
cp cradlewise_unpacked/assets/certs/amazon_root_ca1.pem \
   custom_components/cradlewise/certs/
cp cradlewise_unpacked/assets/certs/client.pem \
   custom_components/cradlewise/certs/
cp cradlewise_unpacked/assets/certs/client.key \
   custom_components/cradlewise/certs/
```

### 2. Find the crib's LAN IP

Look for a device named **Cradlewise** (or with a Cradlewise MAC prefix) in your
router's DHCP table. The IP is typically static or you can assign a reservation.

### 3. Configure the integration

- Go to **Settings → Devices & Services → Cradlewise → Reconfigure**
- Enter the crib's LAN IP in the **Crib LAN IP** field
- Restart Home Assistant

The `Camera` entity will appear under your crib device and update with a live JPEG
snapshot whenever the HA camera card or dashboard requests one.

> **Note:** The certs are device-account-specific. Keep them private and do not
> commit them to version control.
