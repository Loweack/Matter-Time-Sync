# Matter Time Sync

A Home Assistant custom integration to synchronize time on Matter devices that support the Time Synchronization cluster.

## Overview

Many Matter devices have an internal clock that needs to be synchronized for accurate timestamps in logs, schedules, and sensor readings. This integration provides an easy way to sync time on all your Matter devices, either manually via button entities or automatically at configurable intervals.

## Features

- **Automatic Device Discovery**: Discovers all Matter devices from your Matter Server
- **Time Sync Cluster Detection**: Automatically identifies which devices support time synchronization
- **Button Entities**: Creates a sync button for each compatible device
- **Auto-Sync**: Optionally synchronize all devices at regular intervals (15 min to 24 hours)
- **Device Filtering**: Filter which devices get sync buttons by name
- **Auto-Discovery of New Devices**: New Matter devices are automatically detected during auto-sync
- **Timezone Support**: Uses your Home Assistant timezone or a custom one

## Requirements

- Home Assistant 2024.1.0 or newer
- Matter Server (usually installed via the Matter integration)
- Matter devices that support the Time Synchronization cluster (0x0038)

## Installation

### Manual Installation

1. Download the latest release
2. Extract the `matter_time_sync` folder to your `custom_components` directory
3. Restart Home Assistant
4. Go to Settings > Devices & Services > Add Integration
5. Search for "Matter Time Sync"

### HACS Installation

1. Add this repository as a custom repository in HACS
2. Install "Matter Time Sync"
3. Restart Home Assistant
4. Add the integration via Settings > Devices & Services

## Configuration

### Initial Setup

When adding the integration, you can configure:

| Option | Description |
|--------|-------------|
| **Matter Server WebSocket URL** | Auto-detected if Matter integration is present (default: `ws://core-matter-server:5580/ws`) |
| **Timezone** | Timezone for synchronization (default: Home Assistant timezone) |
| **Device Filter** | Comma-separated list of terms to filter devices (empty = all devices) |
| **Enable automatic synchronization** | Enable auto-sync at regular intervals |
| **Synchronization interval** | How often to sync (15 min, 30 min, 1 h, 2 h, 6 h, 12 h, 24 h) |
| **Only devices with Time Sync support** | Only create buttons for devices that support Time Sync cluster |

### Device Filter Examples

The device filter matches device names (case-insensitive, partial match):

- `alpstuga` - Only devices containing "alpstuga" in their name
- `alpstuga, ikea` - Devices containing "alpstuga" OR "ikea"
- *(empty)* - All devices

### Options

All settings can be changed later via the integration options (Settings > Devices & Services > Matter Time Sync > Configure).

## Usage

### Button Entities

Each compatible Matter device gets a button entity named `[Device Name] Sync Time`. Press the button to synchronize that device's time.

Example entity IDs:
- `button.alpstuga_air_quality_monitor_sync_time`
- `button.vindstyrka_sync_time`

### Services

#### matter_time_sync.sync_time

Synchronize time on a specific Matter device by node ID.

```yaml
service: matter_time_sync.sync_time
data:
  node_id: 12
  endpoint: 0  # optional, default: 0
```

#### matter_time_sync.sync_all

Synchronize time on all filtered Matter devices.

```yaml
service: matter_time_sync.sync_all
```

#### matter_time_sync.refresh_devices

Search for new Matter devices and create buttons for them.

```yaml
service: matter_time_sync.refresh_devices
```

### Automation Examples

#### Sync all devices at midnight

```yaml
automation:
  - alias: "Sync Matter Time at Midnight"
    trigger:
      - platform: time
        at: "00:00:00"
    action:
      - service: matter_time_sync.sync_all
```

#### Sync after Home Assistant restart

```yaml
automation:
  - alias: "Sync Matter Time on Startup"
    trigger:
      - platform: homeassistant
        event: start
    action:
      - delay: "00:01:00"  # Wait for Matter Server to be ready
      - service: matter_time_sync.sync_all
```

## Device Compatibility

The integration automatically detects which devices support the Time Synchronization cluster. Devices that are known to support it include:

- IKEA ALPSTUGA air quality monitor
- IKEA VINDSTYRKA air quality sensor
- Other Matter devices with Time Sync cluster (0x0038)

Devices without Time Sync support (like simple sensors, buttons, or plugs) will be skipped unless you disable the "Only devices with Time Sync support" option.

## Troubleshooting

### No devices found

- Check that the Matter Server is running and accessible
- Verify the WebSocket URL is correct
- Check Home Assistant logs for connection errors

### Devices showing as "Matter Node X"

- The device may not expose product information
- Check if the device has a user-defined name in Home Assistant
- The name will update after the device is properly commissioned

### Sync fails with "UnsupportedCluster"

- The device does not support the Time Synchronization cluster
- Enable "Only devices with Time Sync support" to filter these out

### Sync fails with "Node X is not (yet) available"

- The device is offline or not responding
- Check the device's battery or power connection
- Try re-commissioning the device

## Logging

Enable debug logging for detailed information:

```yaml
logger:
  default: info
  logs:
    custom_components.matter_time_sync: debug
```

## Version History

### v3.0.0
- Added automatic device discovery during auto-sync
- Added Time Sync cluster detection
- Added device filter option
- Added "Only Time Sync devices" option
- Fixed device naming (unique entity names)
- Fixed attribute parsing for Matter Server
- Added refresh_devices service
- Added complete German and English translations

### v2.0.0
- Added button entities for each device
- Added auto-discovery of Matter devices
- Added timezone selection dropdown
- Added auto-sync feature

### v1.0.0
- Initial release with manual node_id service

## License

MIT License

## Contributing

Contributions are welcome! Please open an issue or pull request on GitHub.
