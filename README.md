# Matter Time Sync for Home Assistant

![Version](https://img.shields.io/badge/version-2.0.3-blue)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Component-orange)

A native Home Assistant custom component to synchronize **Time** and **Timezone** on Matter devices that support the Time Synchronization cluster.

This component communicates directly with the Matter Server Add-on (or standalone container) via WebSocket, ensuring your devices always display the correct local time. I originally created this solution out of frustration with the **IKEA ALPSTUGA**'s inability to sync time (via Home Assistant), but it works across various Matter devices with automatic discovery and flexible scheduling options.

## 🙏🏻 Acknowledgments
🫰🏻 A big "thank you" to [@Lexorius](https://github.com/Lexorius) and [@miketth](https://github.com/miketth) for their help with this v2.

## ⚠️ WARNING
### Breaking Change: Users upgrading from v1.0.4 must delete and re-add the integration (this does not apply to fresh installs or users already on v2.0.0).
Go to Settings > Devices & Services, find the Matter Time Sync entry, delete it, and add it again.<br/>
This is required to load the new configuration options properly.

## ✨ Features

*   **🔍 Automatic Device Discovery**: Discovers all Matter devices from your Matter Server and identifies which support time synchronization
*   **🔘 Button Entities**: Creates a sync button for each compatible device
*   **⚡ Auto-Sync**: Optionally synchronize all devices at regular intervals (15 min to 24 hours)
*   **🎯 Device Filtering**: Filter which devices get sync buttons by name
*   **⚡ Native Async**: Built using Home Assistant's native `aiohttp` engine for high performance and stability
*   **🛠️ Zero Dependencies**: Does not require the heavy `chip` SDK or external `websocket-client` libraries
*   **⚙️ UI Configuration**: Configure everything directly via the Home Assistant interface
*   **🌍 Complete Sync**: Synchronizes Time Zone (Standard Offset) and UTC Time (Microsecond precision)

⚠️ You have to expose the TCP port 5580. To do this, go to `Settings` → `Add-ons` → `Matter Server` → `Configuration` → `Network` and add 5580 to expose the Matter Server WebSocket port.

## 📥 Installation

### Option 1: HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Loweack&repository=Matter-Time-Sync&category=integration)

1.  Open **HACS** in Home Assistant.
2.  Go to the **Integrations** section.
3.  Click the menu (three dots) in the top right corner and select **Custom repositories**.
4.  Paste the URL of this GitHub repository (https://github.com/Loweack/Matter-Time-Sync).
5.  Select **Integration** as the category and click **Add**.
6.  Click **Download** on the new "Matter Time Sync" card.
7.  **Restart Home Assistant**.

### Option 2: Manual Installation

1.  Download the latest release from this repository.
2.  Copy the `custom_components/matter_time_sync` folder into your Home Assistant's `homeassistant/custom_components/` directory.
3.  **Restart Home Assistant**.

---

## ⚙️ Configuration

1.  Navigate to **Settings** > **Devices & Services**.
2.  Click **+ Add Integration**.
3.  Search for **Matter Time Sync**.
4.  Enter your configuration details:

| Option | Description | Default |
|--------|-------------|---------|
| **WebSocket Address** | The address of your Matter Server | `ws://core-matter-server:5580/ws` (auto-detected) |
| **Timezone** | Your IANA timezone (e.g., `Europe/Paris`, `America/New_York`) | Home Assistant timezone |
| **Device Filter** | Comma-separated list of terms to filter devices (empty = all devices) | *(empty)* |
| **Enable automatic synchronization** | Enable auto-sync at regular intervals | Disabled |
| **Synchronization interval** | How often to sync all devices | 1 hour |
| **Only devices with Time Sync support** | Only create buttons for devices that support Time Sync cluster (0x0038) | Enabled |

5.  Click **Submit**.

### Device Filter Examples

The device filter matches device names (case-insensitive, partial match):

- `alpstuga` - Only devices containing "alpstuga" in their name
- `alpstuga, ikea` - Devices containing "alpstuga" OR "ikea"
- *(empty)* - All devices

### Reconfiguration

All settings can be changed later via **Settings** > **Devices & Services** > **Matter Time Sync** > **Configure**.

---

## 🚀 Usage

### Button Entities

Each compatible Matter device gets a button entity named `[Device Name] Sync Time`. Press the button to synchronize that device's time immediately.

Example entity IDs:
- `button.alpstuga_air_quality_monitor_sync_time`
- `button.vindstyrka_sync_time`

### Services

#### `matter_time_sync.sync_time`

Synchronizes time on a specific Matter device by node ID.

**Parameters:**
*   `node_id` (Required): The Matter Node ID of the device (integer).
*   `endpoint` (Optional): The endpoint ID (default: `0`).

**Example:**

```yaml
service: matter_time_sync.sync_time
data:
  node_id: 7
  endpoint: 0
```

#### `matter_time_sync.sync_all`

Synchronize time on all filtered Matter devices at once.

```yaml
service: matter_time_sync.sync_all
```

#### `matter_time_sync.refresh_devices`

Search for new Matter devices and create buttons for them (useful after adding new devices).

```yaml
service: matter_time_sync.refresh_devices
```

### Automation Examples

#### Sync IKEA ALPSTUGA on Schedule and Device Availability

Synchronizes time every Sunday at 03:15 AM and when the device comes online:

```yaml
alias: "[TIME] Sync IKEA ALPSTUGA"
description: >-
  Synchronizes the time on the IKEA ALPSTUGA on Sundays at 03:15 AM and whenever
  the device becomes available after being unavailable.
triggers:
  - at: "03:15:00"
    trigger: time
    weekday:
      - sun
  - entity_id:
      - switch.alpstuga_air_quality_monitor
    from:
      - unavailable
    to: null
    trigger: state
actions:
  - delay:
      hours: 0
      minutes: 0
      seconds: 5
      milliseconds: 0
  - action: matter_time_sync.sync_time
    data:
      node_id: 7
      endpoint: 0
mode: restart
```

#### Sync All Devices at Midnight

```yaml
alias: "Sync Matter Time at Midnight"
trigger:
  - platform: time
    at: "00:00:00"
action:
  - service: matter_time_sync.sync_all
```

#### Sync After Home Assistant Restart

```yaml
alias: "Sync Matter Time on Startup"
trigger:
  - platform: homeassistant
    event: start
action:
  - delay: "00:01:00"  # Wait for Matter Server to be ready
  - service: matter_time_sync.sync_all
```

---

## 🔧 Device Compatibility

The integration automatically detects which devices support the Time Synchronization cluster (0x0038). Devices that are known to support it include:

- **IKEA ALPSTUGA** air quality monitor
- **IKEA VINDSTYRKA** air quality sensor
- Other Matter devices with Time Sync cluster support

Devices without Time Sync support (like simple sensors, buttons, or plugs) will be skipped unless you disable the "Only devices with Time Sync support" option.

---

## 🐛 Troubleshooting

### No devices found

- Check that the Matter Server is running and accessible
- Verify the WebSocket URL is correct (default: `ws://core-matter-server:5580/ws`)
- Check Home Assistant logs for connection errors

### Devices showing as "Matter Node X"

- The device may not expose product information
- Check if the device has a user-defined name in Home Assistant
- The name will update after the device is properly commissioned

### Sync fails with "UnsupportedCluster"

- The device does not support the Time Synchronization cluster
- Enable "Only devices with Time Sync support" to filter these out automatically

### Sync fails with "Node X is not (yet) available"

- The device is offline or not responding
- Check the device's battery or power connection
- Try re-commissioning the device in the Matter integration

---

## 📝 Logging

Enable debug logging for detailed information:

```yaml
logger:
  default: info
  logs:
    custom_components.matter_time_sync: debug
```

---

## 📋 Version History

### v2.0.3
- Fixed autosync
- Improved time sync

### v2.0.2
- Added support for the new Matter Server payload format (camelCase) while maintaining backward compatibility
- Added a cooldown/debounce (with concurrency lock) to prevent repeated time-sync requests from rapid clicks

### v2.0.1
- Fixed DST Offset

### v2.0.0
- Added automatic device discovery during auto-sync
- Added Time Sync cluster detection
- Added device filter option
- Added "Only Time Sync devices" option
- Added button entities for each device
- Added refresh_devices service
- Added sync_all service
- Added timezone selection dropdown
- Added configurable auto-sync intervals (15 min to 24 hours)
- Added complete German and English translations
- Fixed device naming (unique entity names)
- Fixed attribute parsing for Matter Server

### v1.0.4
- Initial stable release
- Basic time synchronization via service calls
- WebSocket communication with Matter Server
- UI configuration support

---
