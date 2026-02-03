"""Matter Time Sync Integration (Native Async)."""
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_track_time_interval
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_SYNC_TIME,
    SERVICE_SYNC_ALL,
    SERVICE_REFRESH_DEVICES,
    CONF_AUTO_SYNC_ENABLED,
    CONF_AUTO_SYNC_INTERVAL,
    CONF_DEVICE_FILTER,
    CONF_ONLY_TIME_SYNC_DEVICES,
    DEFAULT_AUTO_SYNC_ENABLED,
    DEFAULT_AUTO_SYNC_INTERVAL,
    DEFAULT_DEVICE_FILTER,
    DEFAULT_ONLY_TIME_SYNC_DEVICES,
    PLATFORMS,
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

    # 2. Get config values
    device_filter_str = entry.data.get(CONF_DEVICE_FILTER, DEFAULT_DEVICE_FILTER)
    device_filters = [f.strip().lower() for f in device_filter_str.split(",") if f.strip()]
    only_time_sync = entry.data.get(CONF_ONLY_TIME_SYNC_DEVICES, DEFAULT_ONLY_TIME_SYNC_DEVICES)
    auto_sync_enabled = entry.data.get(CONF_AUTO_SYNC_ENABLED, DEFAULT_AUTO_SYNC_ENABLED)
    auto_sync_interval = entry.data.get(CONF_AUTO_SYNC_INTERVAL, DEFAULT_AUTO_SYNC_INTERVAL)

    _LOGGER.info(
        "Matter Time Sync setup: auto_sync=%s, interval=%d min, filter=%s, only_time_sync=%s",
        auto_sync_enabled,
        auto_sync_interval,
        device_filters if device_filters else "[all]",
        only_time_sync,
    )

    # 3. Store data in hass.data so button.py can access it
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "device_filters": device_filters,
        "only_time_sync_devices": only_time_sync,
        "unsub_timer": None,  # Will hold the timer unsubscribe function
    }

    # 4. Forward entry setup to platforms (load button.py)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 5. Define Service Handlers (using the coordinator)
    async def handle_sync_time(call: ServiceCall) -> None:
        """Handle the sync_time service call."""
        node_id = call.data["node_id"]
        endpoint = call.data["endpoint"]
        await coordinator.async_sync_time(node_id, endpoint)

    async def handle_sync_all(call: ServiceCall) -> None:
        """Handle the sync_all service call."""
        await coordinator.async_sync_all_devices()

    async def handle_refresh_devices(call: ServiceCall) -> None:
        """Handle the refresh_devices service call."""
        # Refresh the node cache
        await coordinator.async_get_matter_nodes()

        # Add new buttons
        from .button import async_check_new_devices
        await async_check_new_devices(hass, entry.entry_id)

    # 6. Register Services (only once)
    if not hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME):
        hass.services.async_register(
            DOMAIN, SERVICE_SYNC_TIME, handle_sync_time, schema=SYNC_TIME_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SYNC_ALL):
        hass.services.async_register(DOMAIN, SERVICE_SYNC_ALL, handle_sync_all)
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_DEVICES):
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_DEVICES, handle_refresh_devices)

    # 7. Setup Auto-Sync Timer if enabled
    if auto_sync_enabled:
        await _setup_auto_sync_timer(hass, entry, coordinator, auto_sync_interval)

    # 8. Listen for options updates
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _setup_auto_sync_timer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: MatterTimeSyncCoordinator,
    interval_minutes: int,
) -> None:
    """Setup the auto-sync timer."""
    entry_data = hass.data[DOMAIN][entry.entry_id]

    # Cancel existing timer if any
    if entry_data.get("unsub_timer"):
        entry_data["unsub_timer"]()
        entry_data["unsub_timer"] = None
        _LOGGER.debug("Cancelled existing auto-sync timer")

    async def _auto_sync_callback(now) -> None:
        """Callback for auto-sync timer."""
        _LOGGER.info("Auto-sync triggered at %s", now)
        
        # Also check for new devices during auto-sync
        from .button import async_check_new_devices
        new_count = await async_check_new_devices(hass, entry.entry_id)
        if new_count > 0:
            _LOGGER.info("Auto-discovery found %d new devices", new_count)
        
        # Sync all devices
        await coordinator.async_sync_all_devices()

    # Create the timer
    unsub = async_track_time_interval(
        hass,
        _auto_sync_callback,
        timedelta(minutes=interval_minutes),
    )
    entry_data["unsub_timer"] = unsub

    _LOGGER.info(
        "Auto-sync timer started: syncing every %d minutes",
        interval_minutes,
    )

    # Run initial sync after a short delay (give HA time to fully start)
    async def _initial_sync():
        """Run initial sync after startup."""
        import asyncio
        await asyncio.sleep(30)  # Wait 30 seconds after startup
        _LOGGER.info("Running initial auto-sync after startup")
        await coordinator.async_sync_all_devices()

    hass.async_create_task(_initial_sync())


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.info("Configuration updated, reloading Matter Time Sync")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data[DOMAIN].get(entry.entry_id, {})

    # Cancel auto-sync timer
    if entry_data.get("unsub_timer"):
        entry_data["unsub_timer"]()
        _LOGGER.debug("Auto-sync timer cancelled")

    # Disconnect coordinator
    coordinator = entry_data.get("coordinator")
    if coordinator:
        await coordinator.async_disconnect()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

        # Remove services only if no entries remain
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_TIME)
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_ALL)
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_DEVICES)

    return unload_ok
