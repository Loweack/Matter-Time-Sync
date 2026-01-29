# Matter Time Sync for Home Assistant

![Version](https://img.shields.io/badge/version-1.0.4-blue)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Custom%20Component-orange)

A native Home Assistant custom component to synchronize **Time** and **Timezone** on Matter devices. 

This component communicates directly with the Matter Server Add-on (or standalone container) via WebSocket, ensuring your devices always display the correct local time. I originally created this solution out of frustration with the **IKEA ALPSTUGA**'s inability to sync time (via Home Assistant), but it works across various Matter devices. You can even set up automations to instantly sync the time whenever a device is plugged in.

## ✨ Features

*   **⚡ Native Async**: Built using Home Assistant's native `aiohttp` engine for high performance and stability.
*   **🛠️ Zero Dependencies**: Does not require the heavy `chip` SDK or external `websocket-client` libraries.
*   **⚙️ UI Configuration**: Configure your WebSocket URL and Timezone directly via the Home Assistant interface.
*   **🌍 Complete Sync**: Synchronizes:
    *   Time Zone (Standard Offset)
    *   UTC Time (Microsecond precision)

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

1.  Download the repository.
2.  Copy the `custom_components/matter_time_sync` folder into your Home Assistant's `homeassistant/custom_components/` directory.
3.  **Restart Home Assistant**.

---

## ⚙️ Configuration

1.  Navigate to **Settings** > **Devices & Services**.
2.  Click **+ Add Integration**.
3.  Search for **Matter Time Sync**.
4.  Enter your configuration details:
    *   **WebSocket Address**: The address of your Matter Server.
        *   *Default*: `ws://localhost:5580/ws` (Replace `localhost` with the IP of your HA instance, e.g., `ws://192.168.1.2:5580/ws`).
    *   **Timezone**: Your IANA timezone (e.g., `Europe/Paris`, `America/New_York`).
5.  Click **Submit**.

---

## 🚀 Usage

You can sync time on your devices using the `matter_time_sync.sync_time` service in Automations, Scripts, or Developer Tools.

### Service: `matter_time_sync.sync_time`

**Parameters:**
*   `node_id` (Required): The Matter Node ID of the device (integer).
*   `endpoint` (Optional): The endpoint ID (default: `0`).

### Example: Automation (YAML)

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
