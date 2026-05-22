# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **Switch entities**: Rocking, White Noise, Night Light, Auto Amplitude, Auto Volume, Auto Sound Mood, Keep Music During Sleep
- **Number entities**: Rock Amplitude (1–5), Music Volume (0–60), Light Intensity (0–100), Responsivity (1–10)
- **Select entity**: Sound Profile (White Noise, Deep Sleeper, Noisy Room, Sensitive Sleeper)
- **Sleep Insights Lovelace card** (`lovelace/cradlewise-sleep-card.js`) — custom card matching the official Cradlewise app UI:
  - Main view: Day / Week / Month tabs, live sleep stats, mini C-shape timeline, "Preview sleep chart" button
  - Preview chart: scrollable 7-day week strip, C-shape 8 AM→8 AM timeline with sleep/stirring/awake/soothe segments, tap-to-tooltip
  - Preview list: nap session cards with tap-to-expand sub-phase breakdown (Sleep / Stirring / Sleep + Soothed indicator)
  - Month picker: tap the month/year label to jump instantly to any past month/year (up to 2 years back)
  - Week navigation ◀ ▶ for fine-tuning within a month
- **Hardware-in-the-Loop tests** (`tests/test_hilt.py`): real-device validation for rocking, music, night light, amplitude, volume, recipe script, after-sleep automation
- **Unit/functional tests**: coordinator, sensor entities, switch/number entities

### Changed
- Coordinator now surfaces `actuator`, `soundSynth`, `light`, and settings fields for use by switch/number/select entities
- `manifest.json` version bumped to 1.0.0

## [0.3.1] — 2025-05

### Fixed
- Resolved blocking I/O warning in `pycradlewise` when coordinator fetches state on the event loop

## [0.3.0] — 2025-05

### Added
- IoT endpoint passed from APK config to MQTT client
- Local WebRTC camera entity (`camera.py`, `webrtc.py`) — live H.264 1280×720 stream direct from crib LAN
- Mutual TLS cert support for WebRTC (certs extracted from APK)

### Fixed
- Resume live video after Cradlewise phone app disconnects WebRTC session

## [0.2.0] — 2025-04

### Added
- Initial `sensor` and `binary_sensor` entities via cloud MQTT
- Real-time sleep phase, baby present, bouncing, music playing, night light, soothe count, total sleep
- Config flow with email/password authentication
- MQTT REST polling fallback

## [0.1.0] — 2025-04

### Added
- Initial release — basic integration skeleton, HACS support
