"""Matter Time Sync Integration (Native Async)."""
import logging
from datetime import timedelta
from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_SYNC_TIME,
    SERVICE_SYNC_ALL,
    SERVICE_REFRESH_DEVICES,
    PLATFORMS,
    CONF_AUTO_SYNC_ENABLED,
    CONF_AUTO_SYNC_INTERVAL,
    DEFAULT_AUTO_SYNC_ENABLED,
    DEFAULT_AUTO_SYNC_INTERVAL,
)
from .coordinator import MatterTimeSyncCoordinator

_LOGGER = logging.getLogger(__name__)

# Schema for sync_time service
SYNC_TIME_SCHEMA = vol.Schema({
    vol.Required("node_id"): cv.positive_int,
    vol.Optional("endpoint", default=0): cv.positive_int,
})

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the component via YAML (stub)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # 1. Initialize Coordinator
    coordinator = MatterTimeSyncCoordinator(hass, entry)
    
    # 2. Connect to Matter Server immediately
    connected = await coordinator.async_connect()
    if not connected:
        _LOGGER.error("Failed to connect to Matter Server at startup. Will retry on first command.")
    
    # 3. Store it in hass.data so button.py can access it
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "device_filters": entry.data.get("device_filter", "").split(",") if entry.data.get("device_filter") else [],
        "only_time_sync_devices": entry.data.get("only_time_sync_devices", True),
    }

    # 4. Forward entry setup to platforms (load button.py)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 5. Set up auto-sync timer if enabled
    auto_sync_enabled = entry.data.get(CONF_AUTO_SYNC_ENABLED, DEFAULT_AUTO_SYNC_ENABLED)
    auto_sync_interval = entry.data.get(CONF_AUTO_SYNC_INTERVAL, DEFAULT_AUTO_SYNC_INTERVAL)

    if auto_sync_enabled:
        async def auto_sync_handler(now):
            """Handle auto-sync timer."""
            _LOGGER.info("Auto-sync triggered (interval: %d minutes)", auto_sync_interval)
            try:
                stats = await coordinator.async_sync_all_devices()
                
                # Log summary based on results
                if stats["success"] > 0:
                    _LOGGER.info(
                        "Auto-sync completed: %d devices synced successfully",
                        stats["success"]
                    )
                
                if stats["failed"] > 0:
                    _LOGGER.warning(
                        "Auto-sync: %d devices failed. Errors: %s",
                        stats["failed"],
                        stats["errors"][:3]  # Show first 3 errors
                    )
                
                if stats["success"] == 0 and stats["failed"] == 0 and stats["skipped"] > 0:
                    _LOGGER.debug(
                        "Auto-sync: No devices to sync (%d skipped by filters)",
                        stats["skipped"]
                    )
                    
            except Exception as err:
                # This should rarely happen now (caught in coordinator)
                _LOGGER.error("Auto-sync handler exception: %s", err, exc_info=True)
        
        # Schedule periodic sync
        interval = timedelta(minutes=auto_sync_interval)
        cancel_timer = async_track_time_interval(hass, auto_sync_handler, interval)
        
        # Store the cancel callback
        hass.data[DOMAIN][entry.entry_id]["auto_sync_cancel"] = cancel_timer
        
        _LOGGER.info("Auto-sync enabled with interval: %d minutes", auto_sync_interval)
    else:
        _LOGGER.info("Auto-sync disabled")

    # 6. Define Service Handlers (using the coordinator)
    async def handle_sync_time(call: ServiceCall) -> None:
        """Handle the sync_time service call."""
        node_id = call.data["node_id"]
        endpoint = call.data["endpoint"]
        await coordinator.async_sync_time(node_id, endpoint)

    async def handle_sync_all(call: ServiceCall) -> None:
        """Handle the sync_all service call."""
        _LOGGER.info("Manual sync_all service called")
        stats = await coordinator.async_sync_all_devices()
        _LOGGER.info(
            "Manual sync_all completed: %d synced, %d failed, %d skipped",
            stats["success"],
            stats["failed"],
            stats["skipped"]
        ) 

    async def handle_refresh_devices(call: ServiceCall) -> None:
        """Handle the refresh_devices service call."""
        # Trigger the check for new devices in button.py logic
        await coordinator.async_get_matter_nodes()
        
        # To actually add new buttons, we need to call the method in button.py.
        from .button import async_check_new_devices
        await async_check_new_devices(hass, entry.entry_id)

    # 7. Register Services
    hass.services.async_register(DOMAIN, SERVICE_SYNC_TIME, handle_sync_time, schema=SYNC_TIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SYNC_ALL, handle_sync_all)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_DEVICES, handle_refresh_devices)

    # 8. Listen for config options updates
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    _LOGGER.info("Reloading Matter Time Sync integration due to config changes")
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Get entry data and coordinator
    entry_data = hass.data[DOMAIN].get(entry.entry_id, {})
    coordinator = entry_data.get("coordinator")
    
    # Cancel auto-sync timer if running
    if "auto_sync_cancel" in entry_data:
        cancel = entry_data["auto_sync_cancel"]
        cancel()
        _LOGGER.info("Auto-sync timer cancelled")
    
    # Disconnect from Matter Server
    if coordinator:
        await coordinator.async_disconnect()
        _LOGGER.info("Disconnected from Matter Server")
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Remove services only if no entries remain (optional but good practice)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_TIME)
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_ALL)
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_DEVICES)

    return unload_ok
