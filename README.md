# Matter Time Sync for Home Assistant

![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Component-orange)

A native Home Assistant custom component to synchronize **Time** and **Timezone** on Matter devices that support the Time Synchronization cluster.

This component communicates directly with the Matter Server Add-on (or standalone container) via WebSocket, ensuring your devices always display the correct local time. I originally created this solution out of frustration with the **IKEA ALPSTUGA**'s inability to sync time (via Home Assistant), but it works across various Matter devices with automatic discovery and flexible scheduling options.

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
