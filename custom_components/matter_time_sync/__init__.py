"""Matter Time Sync Integration (Native Async)."""
import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_SYNC_TIME,
    SERVICE_SYNC_ALL,
    SERVICE_REFRESH_DEVICES,  # Make sure to add this to const.py if missing!
    PLATFORMS
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
    
    # 2. Store it in hass.data so button.py can access it
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "device_filters": entry.data.get("device_filter", "").split(",") if entry.data.get("device_filter") else [],
        "only_time_sync_devices": entry.data.get("only_time_sync_devices", True),
    }

    # 3. Forward entry setup to platforms (load button.py)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 4. Define Service Handlers (using the coordinator)
    async def handle_sync_time(call: ServiceCall) -> None:
        """Handle the sync_time service call."""
        node_id = call.data["node_id"]
        endpoint = call.data["endpoint"]
        await coordinator.async_sync_time(node_id, endpoint)

    async def handle_sync_all(call: ServiceCall) -> None:
        """Handle the sync_all service call."""
        # You need to implement async_sync_all in coordinator.py if it's missing
        # For now, we can iterate over known nodes if needed, or better:
        # Add async_sync_all_devices() to your coordinator.
        await coordinator.async_sync_all_devices() 

    async def handle_refresh_devices(call: ServiceCall) -> None:
        """Handle the refresh_devices service call."""
        # Trigger the check for new devices in button.py logic
        # This requires a bit of wiring, usually done by reloading the integration 
        # or calling a specific method on the coordinator that notifies listeners.
        # For simplicity, let's just refresh the node cache:
        await coordinator.async_get_matter_nodes()
        
        # To actually add new buttons, we need to call the method in button.py.
        # This is often done by firing an event or using a dispatcher.
        # For now, let's assume we just refresh the data.
        from .button import async_check_new_devices
        await async_check_new_devices(hass, entry.entry_id)

    # 5. Register Services
    hass.services.async_register(DOMAIN, SERVICE_SYNC_TIME, handle_sync_time, schema=SYNC_TIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SYNC_ALL, handle_sync_all)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_DEVICES, handle_refresh_devices)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Remove services only if no entries remain (optional but good practice)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_TIME)
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_ALL)
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_DEVICES)

    return unload_ok
