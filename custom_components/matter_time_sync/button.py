"""Button platform for Matter Time Sync.

Attaches per-device Sync Time buttons to the existing Matter devices in HA
(so it does NOT create extra devices).

Supports a configurable filter target:
- any: match filter against display name + node label + product name
- display_name: match only the resolved display name (node['name'])
- ha_name: match only if name_source == 'home_assistant'
- matter: match only node label + product name
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN, DEFAULT_FILTER_TARGET
from .coordinator import device_matches_filter, filter_candidates_for_node

_LOGGER = logging.getLogger(__name__)

# Regex to parse the standard Matter device identifier format:
# "deviceid_<FABRIC_HEX>-<NODEID_HEX_16>-MatterNodeDevice"
_MATTER_ID_RE = re.compile(
    r"deviceid_[0-9A-Fa-f]+-([0-9A-Fa-f]{16})-MatterNodeDevice"
)


def _matter_device_identifiers_for_node(
    hass: HomeAssistant, node_id: int
) -> set[tuple[str, str]] | None:
    """Find the existing Matter device identifiers for a node_id.

    Matter devices are registered with identifiers like:
      ("matter", "deviceid_<FABRIC_HEX>-<NODEID_HEX_16>-MatterNodeDevice")

    Returns the identifier set if found, or None.
    """
    device_reg = dr.async_get(hass)
    needle = f"-{node_id:016X}-MatterNodeDevice"

    for dev in device_reg.devices.values():
        for domain, ident in dev.identifiers:
            if domain != "matter":
                continue
            ident_str = str(ident)

            # Fast path: check if the hex node_id is in the identifier
            if needle in ident_str:
                return {(domain, ident_str)}

            # Regex fallback for unusual formats
            m = _MATTER_ID_RE.search(ident_str)
            if m:
                try:
                    parsed_node_id = int(m.group(1), 16)
                except ValueError:
                    continue
                if parsed_node_id == node_id:
                    return {(domain, ident_str)}

    return None


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
    filter_target = entry_data.get("filter_target", DEFAULT_FILTER_TARGET)

    # Store callback for later use (auto-discovery of new devices)
    entry_data["async_add_entities"] = async_add_entities

    # Get all Matter nodes from the server
    nodes = await coordinator.async_get_matter_nodes()

    _LOGGER.info(
        "Matter Time Sync: Found %d nodes, filter=%s, filter_target=%s, only_time_sync=%s",
        len(nodes),
        device_filters if device_filters else "[empty - all devices]",
        filter_target,
        only_time_sync,
    )

    for node in nodes:
        _LOGGER.info(
            "  - Node %s: '%s' (product: %s)",
            node.get("node_id"),
            node.get("name", "?"),
            node.get("product_name", "?"),
        )

    entities: list[MatterTimeSyncButton] = []
    skipped_filter: list[str] = []
    skipped_no_timesync: list[str] = []
    known_node_ids: set[int] = set()

    for node in nodes:
        node_id = node.get("node_id")
        if node_id is None:
            continue

        node_name = node.get("name", f"Matter Node {node_id}")
        has_time_sync = node.get("has_time_sync", False)

        if only_time_sync and not has_time_sync:
            skipped_no_timesync.append(f"{node_name} (node {node_id})")
            continue

        candidates = filter_candidates_for_node(node, filter_target)
        if not device_matches_filter(device_filters, candidates):
            skipped_filter.append(f"{node_name} (node {node_id})")
            continue

        known_node_ids.add(node_id)

        # Try to attach to the existing Matter device in HA
        matter_identifiers = _matter_device_identifiers_for_node(hass, node_id)

        entities.append(
            MatterTimeSyncButton(
                coordinator=coordinator,
                node_id=node_id,
                node_name=node_name,
                device_info=node.get("device_info"),
                matter_identifiers=matter_identifiers,
            )
        )

    if entities:
        async_add_entities(entities)
        _LOGGER.info(
            "Added %d Matter Time Sync buttons: %s",
            len(entities),
            [e.node_name for e in entities],
        )
    else:
        if not nodes:
            _LOGGER.warning("No Matter devices found! Check connection to Matter Server.")
        elif skipped_no_timesync and not any(
            n.get("has_time_sync", False) for n in nodes
        ):
            _LOGGER.warning(
                "No Matter devices with Time Sync support found! "
                "Devices without Time Sync: %s",
                skipped_no_timesync,
            )
        elif device_filters:
            _LOGGER.warning(
                "No Matter devices matched filter '%s'. "
                "Devices with Time Sync: %s",
                ", ".join(device_filters),
                [n.get("name") for n in nodes if n.get("has_time_sync", False)],
            )
        else:
            _LOGGER.warning(
                "No Matter devices to sync (all filtered or no Time Sync support)"
            )

    if skipped_no_timesync:
        _LOGGER.info(
            "Skipped %d devices (no Time Sync cluster): %s",
            len(skipped_no_timesync),
            skipped_no_timesync,
        )

    if skipped_filter:
        _LOGGER.info(
            "Skipped %d devices (filter): %s",
            len(skipped_filter),
            skipped_filter,
        )

    # Store known node IDs for auto-discovery
    entry_data["known_node_ids"] = known_node_ids


async def async_check_new_devices(
    hass: HomeAssistant,
    entry_id: str,
) -> int:
    """Check for new Matter devices and add buttons for them."""
    entry_data = hass.data[DOMAIN].get(entry_id)
    if not entry_data:
        return 0

    coordinator = entry_data["coordinator"]
    device_filters = entry_data.get("device_filters", [])
    only_time_sync = entry_data.get("only_time_sync_devices", True)
    filter_target = entry_data.get("filter_target", DEFAULT_FILTER_TARGET)
    known_node_ids: set[int] = entry_data.get("known_node_ids", set())
    async_add_entities = entry_data.get("async_add_entities")

    if not async_add_entities:
        _LOGGER.debug("No async_add_entities callback available")
        return 0

    nodes = await coordinator.async_get_matter_nodes()
    new_entities: list[MatterTimeSyncButton] = []

    for node in nodes:
        node_id = node.get("node_id")
        if node_id is None or node_id in known_node_ids:
            continue

        node_name = node.get("name", f"Matter Node {node_id}")
        has_time_sync = node.get("has_time_sync", False)

        if only_time_sync and not has_time_sync:
            _LOGGER.debug("New device %s skipped (no Time Sync support)", node_name)
            continue

        candidates = filter_candidates_for_node(node, filter_target)
        if not device_matches_filter(device_filters, candidates):
            _LOGGER.debug("New device %s skipped (doesn't match filter)", node_name)
            continue

        _LOGGER.info("Discovered new Matter device: %s (node %s)", node_name, node_id)
        known_node_ids.add(node_id)

        matter_identifiers = _matter_device_identifiers_for_node(hass, node_id)

        new_entities.append(
            MatterTimeSyncButton(
                coordinator=coordinator,
                node_id=node_id,
                node_name=node_name,
                device_info=node.get("device_info"),
                matter_identifiers=matter_identifiers,
            )
        )

    if new_entities:
        async_add_entities(new_entities)
        _LOGGER.info(
            "Added %d new Matter Time Sync buttons: %s",
            len(new_entities),
            [e.node_name for e in new_entities],
        )

    entry_data["known_node_ids"] = known_node_ids
    return len(new_entities)


class MatterTimeSyncButton(ButtonEntity):
    """Button to sync time on a Matter device."""

    _attr_icon = "mdi:clock-sync"
    _attr_has_entity_name = False

    _PRESS_COOLDOWN_SECONDS = 2.0

    def __init__(
        self,
        coordinator,
        node_id: int,
        node_name: str,
        device_info: dict | None = None,
        matter_identifiers: set[tuple[str, str]] | None = None,
    ) -> None:
        """Initialize the button."""
        self._coordinator = coordinator
        self._node_id = node_id
        self._node_name = node_name

        # Protect against concurrent async_press calls and keep a cooldown window.
        self._press_lock = asyncio.Lock()
        self._last_press_ts: float = 0.0

        name_slug = slugify(node_name)

        self._attr_unique_id = f"matter_time_sync_{node_id}"
        self._attr_name = f"{node_name} Sync Time"
        self.entity_id = f"button.{name_slug}_sync_time"

        # Attach to the existing Matter device if found,
        # otherwise create a standalone device entry as fallback.
        if matter_identifiers:
            self._attr_device_info = DeviceInfo(identifiers=matter_identifiers)
        elif device_info:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, str(node_id))},
                name=node_name,
                manufacturer=device_info.get("vendor_name", "Unknown"),
                model=device_info.get("product_name", "Matter Device"),
            )

    @property
    def node_name(self) -> str:
        """Return the node name."""
        return self._node_name

    @property
    def node_id(self) -> int:
        """Return the node ID."""
        return self._node_id

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
        _LOGGER.debug("Button pressed for node %s (%s)", self._node_id, self._node_name)

        now = time.monotonic()
        if (now - self._last_press_ts) < self._PRESS_COOLDOWN_SECONDS:
            _LOGGER.debug(
                "Ignoring rapid repeat for node %s (cooldown active)", self._node_id
            )
            return

        async with self._press_lock:
            now = time.monotonic()
            if (now - self._last_press_ts) < self._PRESS_COOLDOWN_SECONDS:
                _LOGGER.debug(
                    "Ignoring rapid repeat for node %s (cooldown active, double-check)",
                    self._node_id,
                )
                return

            self._last_press_ts = now

            _LOGGER.info(
                "Syncing time for Matter node %s (%s)", self._node_id, self._node_name
            )
            success = await self._coordinator.async_sync_time(
                self._node_id, endpoint=None
            )
            if success:
                _LOGGER.info(
                    "Time sync successful for %s (node %s)",
                    self._node_name,
                    self._node_id,
                )
            else:
                _LOGGER.error(
                    "Time sync failed for %s (node %s)",
                    self._node_name,
                    self._node_id,
                )