"""Constants for Matter Time Sync integration."""

from homeassistant.const import Platform

DOMAIN = "matter_time_sync"

# Platforms
PLATFORMS: list[Platform] = [Platform.BUTTON]

# Config keys
CONF_WS_URL = "ws_url"
CONF_TIMEZONE = "timezone"
CONF_DEVICE_FILTER = "device_filter"
CONF_AUTO_SYNC_ENABLED = "auto_sync_enabled"
CONF_AUTO_SYNC_INTERVAL = "auto_sync_interval"
CONF_ONLY_TIME_SYNC_DEVICES = "only_time_sync_devices"

# Defaults
DEFAULT_WS_URL = "ws://core-matter-server:5580/ws"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_DEVICE_FILTER = ""  # Empty = all devices
DEFAULT_AUTO_SYNC_ENABLED = False
DEFAULT_AUTO_SYNC_INTERVAL = 60  # Minutes
DEFAULT_ONLY_TIME_SYNC_DEVICES = True  # Only devices with Time Sync cluster

# Auto-sync interval options (in minutes)
AUTO_SYNC_INTERVALS = [15, 30, 60, 120, 360, 720, 1440]  # 15min to 24h

# Matter Cluster IDs
TIME_SYNC_CLUSTER_ID = 0x0038  # Time Synchronization Cluster (56 decimal)

# Service names
SERVICE_SYNC_TIME = "sync_time"
SERVICE_SYNC_ALL = "sync_all"
SERVICE_REFRESH_DEVICES = "refresh_devices"
