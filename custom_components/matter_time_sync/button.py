"""Button platform for Matter Time Sync."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Convert text to a slug suitable for entity IDs."""
    # Convert to lowercase
    text = text.lower()
    # Replace spaces and special chars with underscores
    text = re.sub(r'[^a-z0-9]+', '_', text)
    # Remove leading/trailing underscores
    text = text.strip('_')
    # Limit length
    return text[:50] if len(text) > 50 else text


def device_matches_filter(device_name: str, filters: list[str]) -> bool:
    """
    Check if a device name matches any of the filter terms.
    
    Uses case-insensitive partial matching.
    If filters is empty, all devices match.
    """
    if not filters:
        return True
    
    device_name_lower = device_name.lower()
    
    for filter_term in filters:
        if filter_term in device_name_lower:
            return True
    
    return False


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Matter Time Sync buttons from a config entry."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]
    device_filters = entry_data.get("device_filters", [])
    only_time_sync = entry_data.get("only_time_sync_devices", True)
    
    # Store callback for later use (auto-discovery of new devices)
    entry_data["async_add_entities"] = async_add_entities
    
    # Get all Matter nodes from the server
    nodes = await coordinator.async_get_matter_nodes()
    
    _LOGGER.info(
        "Matter Time Sync: Found %d nodes from Matter Server, filter: %s, only_time_sync: %s",
        len(nodes),
        device_filters if device_filters else "[empty - all devices]",
        only_time_sync
    )
    
    # Log all found nodes for debugging
    for node in nodes:
        _LOGGER.info(
            "  - Node %s: '%s' (product: %s)",
            node.get("node_id"),
            node.get("name", "?"),
            node.get("product_name", "?")
        )
    
    entities = []
    skipped_filter = []
    skipped_no_timesync = []
    known_node_ids = set()
    
    for node in nodes:
        node_id = node.get("node_id")
        node_name = node.get("name", f"Matter Node {node_id}")
        has_time_sync = node.get("has_time_sync", False)
        
        # Skip devices without Time Sync cluster (if option is enabled)
        if only_time_sync and not has_time_sync:
            skipped_no_timesync.append(f"{node_name} (node {node_id})")
            continue
        
        # Apply device filter
        if not device_matches_filter(node_name, device_filters):
            skipped_filter.append(f"{node_name} (node {node_id})")
            continue
        
        # Track this node as known
        known_node_ids.add(node_id)
        
        entities.append(
            MatterTimeSyncButton(
                coordinator=coordinator,
                node_id=node["node_id"],
                node_name=node_name,
                device_info=node.get("device_info"),
            )
        )
    
    if entities:
        async_add_entities(entities)
        _LOGGER.info(
            "Added %d Matter Time Sync buttons: %s",
            len(entities),
            [e._node_name for e in entities]
        )
    else:
        if not nodes:
            _LOGGER.warning(
                "No Matter devices found! Check connection to Matter Server."
            )
        elif skipped_no_timesync and not any(n.get("has_time_sync", False) for n in nodes):
            _LOGGER.warning(
                "No Matter devices with Time Sync support found! "
                "Devices without Time Sync: %s",
                skipped_no_timesync
            )
        elif device_filters:
            _LOGGER.warning(
                "No Matter devices matched filter '%s'. "
                "Devices with Time Sync: %s",
                ", ".join(device_filters),
                [n.get("name") for n in nodes if n.get("has_time_sync", False)]
            )
        else:
            _LOGGER.warning(
                "No Matter devices to sync (all filtered or no Time Sync support)"
            )
    
    if skipped_no_timesync:
        _LOGGER.info(
            "Skipped %d devices (no Time Sync cluster): %s",
            len(skipped_no_timesync),
            skipped_no_timesync
        )
    
    if skipped_filter:
        _LOGGER.info(
            "Skipped %d devices (filter): %s",
            len(skipped_filter),
            skipped_filter
        )
    
    # Store known node IDs for auto-discovery
    entry_data["known_node_ids"] = known_node_ids


async def async_check_new_devices(
    hass: HomeAssistant,
    entry_id: str,
) -> int:
    """Check for new Matter devices and add buttons for them.
    
    Returns the number of new devices added.
    """
    entry_data = hass.data[DOMAIN].get(entry_id)
    if not entry_data:
        return 0
    
    coordinator = entry_data["coordinator"]
    device_filters = entry_data.get("device_filters", [])
    only_time_sync = entry_data.get("only_time_sync_devices", True)
    known_node_ids = entry_data.get("known_node_ids", set())
    async_add_entities = entry_data.get("async_add_entities")
    
    if not async_add_entities:
        _LOGGER.debug("No async_add_entities callback available")
        return 0
    
    # Get current nodes
    nodes = await coordinator.async_get_matter_nodes()
    
    new_entities = []
    
    for node in nodes:
        node_id = node.get("node_id")
        
        # Skip already known nodes
        if node_id in known_node_ids:
            continue
        
        node_name = node.get("name", f"Matter Node {node_id}")
        has_time_sync = node.get("has_time_sync", False)
        
        # Skip devices without Time Sync cluster (if option is enabled)
        if only_time_sync and not has_time_sync:
            _LOGGER.debug(
                "New device %s skipped (no Time Sync support)",
                node_name
            )
            continue
        
        # Apply device filter
        if not device_matches_filter(node_name, device_filters):
            _LOGGER.debug(
                "New device %s skipped (doesn't match filter)",
                node_name
            )
            continue
        
        # This is a new device that matches our criteria!
        _LOGGER.info(
            "Discovered new Matter device: %s (node %s)",
            node_name, node_id
        )
        
        known_node_ids.add(node_id)
        
        new_entities.append(
            MatterTimeSyncButton(
                coordinator=coordinator,
                node_id=node_id,
                node_name=node_name,
                device_info=node.get("device_info"),
            )
        )
    
    if new_entities:
        async_add_entities(new_entities)
        _LOGGER.info(
            "Added %d new Matter Time Sync buttons: %s",
            len(new_entities),
            [e._node_name for e in new_entities]
        )
    
    # Update known node IDs
    entry_data["known_node_ids"] = known_node_ids
    
    return len(new_entities)


class MatterTimeSyncButton(ButtonEntity):
    """Button to sync time on a Matter device."""

    _attr_icon = "mdi:clock-sync"
    # Set to False so we control the full entity name ourselves
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator,
        node_id: int,
        node_name: str,
        device_info: dict | None = None,
    ) -> None:
        """Initialize the button."""
        self._coordinator = coordinator
        self._node_id = node_id
        self._node_name = node_name
        
        # Create a slug from the device name for the entity_id
        name_slug = slugify(node_name)
        
        # Unique ID for the entity - must be truly unique
        self._attr_unique_id = f"matter_time_sync_{node_id}"
        
        # Full entity name including device name
        # This will result in: "IKEA Alpstuga Sync Time" instead of just "Sync Time"
        self._attr_name = f"{node_name} Sync Time"
        
        # Suggested entity_id (HA may adjust if duplicate)
        # This helps get: button.alpstuga_sync_time instead of button.sync_time
        self.entity_id = f"button.{name_slug}_sync_time"
        
        # Device info for proper device grouping in HA
        if device_info:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, str(node_id))},
                name=node_name,
                manufacturer=device_info.get("vendor_name", "Unknown"),
                model=device_info.get("product_name", "Matter Device"),
            )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "node_id": self._node_id,
            "device_name": self._node_name,
            "integration": DOMAIN,
        }

    async def async_press(self) -> None:
        """Handle the button press - sync time."""
        _LOGGER.info("Syncing time for Matter node %s (%s)", self._node_id, self._node_name)
        success = await self._coordinator.async_sync_time(self._node_id, endpoint=0)
        if success:
            _LOGGER.info("Time sync successful for %s (node %s)", self._node_name, self._node_id)
        else:
            _LOGGER.error("Time sync failed for %s (node %s)", self._node_name, self._node_id)
