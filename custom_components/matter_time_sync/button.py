"""Button platform for Matter Time Sync.

This version tries to *attach* the per-device Sync Time button to the **existing**
Matter device in Home Assistant (so it does NOT create extra devices).

It does this by finding the Matter device-registry identifier for the node_id and
using that identifier in DeviceInfo.identifiers, which causes HA to merge the entity
into the existing device.
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

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Typical HA Matter identifier example:
#   deviceid_<FABRIC_HEX>-<NODEID_HEX_16>-MatterNodeDevice
# We parse node_id from that to be robust against minor formatting differences.
_MATTER_ID_RE = re.compile(r"deviceid_[0-9A-Fa-f]+-([0-9A-Fa-f]{16})-MatterNodeDevice")


def slugify(text: str) -> str:
    """Convert text to a slug suitable for entity IDs."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:50] if len(text) > 50 else text


def device_matches_filter(device_name: str, filters: list[str]) -> bool:
    """Case-insensitive partial matching against filter terms."""
    if not filters:
        return True
    dn = device_name.lower()
    return any(term and term in dn for term in filters)


def _matter_device_identifiers_for_node(
    hass: HomeAssistant,
    node_id: int,
) -> set[tuple[str, str]] | None:
    """Return the existing Matter device identifiers for a node_id, if found."""
    device_reg = dr.async_get(hass)

    # Fast path: common needle match
    needle = f"-{node_id:016X}-MatterNodeDevice"

    for dev in device_reg.devices.values():
        for domain, ident in dev.identifiers:
            if domain != "matter":
                continue

            ident_str = str(ident)

            # 1) Simple substring check (fast)
            if needle in ident_str:
                return {(domain, ident_str)}

            # 2) Robust parse (regex) – handles cases where the substring format changes slightly
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
    device_filters = [t.strip().lower() for t in entry_data.get("device_filters", []) if t.strip()]
    only_time_sync = entry_data.get("only_time_sync_devices", True)

    # Store callback for later use (auto-discovery of new devices)
    entry_data["async_add_entities"] = async_add_entities

    nodes = await coordinator.async_get_matter_nodes()

    _LOGGER.info(
        "Matter Time Sync: Found %d nodes from Matter Server, filter=%s, only_time_sync=%s",
        len(nodes),
        device_filters if device_filters else "[empty - all devices]",
        only_time_sync,
    )

    entities: list[MatterTimeSyncButton] = []
    known_node_ids: set[int] = set()

    for node in nodes:
        node_id = node.get("node_id")
        if node_id is None:
            continue

        node_name = node.get("name", f"Matter Node {node_id}")
        has_time_sync = node.get("has_time_sync", False)

        if only_time_sync and not has_time_sync:
            continue
        if not device_matches_filter(node_name, device_filters):
            continue

        known_node_ids.add(node_id)

        matter_identifiers = _matter_device_identifiers_for_node(hass, node_id)
        if not matter_identifiers:
            _LOGGER.debug(
                "No existing Matter device found in HA for node_id=%s (%s). "
                "Button will be created without a device (no extra device will be created). ",
                node_id,
                node_name,
            )

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
        _LOGGER.info("Added %d Matter Time Sync buttons.", len(entities))
    else:
        _LOGGER.warning("No Matter Time Sync buttons created (filtered out or no Time Sync support).")

    entry_data["known_node_ids"] = known_node_ids


async def async_check_new_devices(hass: HomeAssistant, entry_id: str) -> int:
    """Check for new Matter devices and add buttons for them."""
    entry_data = hass.data[DOMAIN].get(entry_id)
    if not entry_data:
        return 0

    coordinator = entry_data["coordinator"]
    device_filters = [t.strip().lower() for t in entry_data.get("device_filters", []) if t.strip()]
    only_time_sync = entry_data.get("only_time_sync_devices", True)
    known_node_ids: set[int] = entry_data.get("known_node_ids", set())
    async_add_entities = entry_data.get("async_add_entities")

    if not async_add_entities:
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
            continue
        if not device_matches_filter(node_name, device_filters):
            continue

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
        _LOGGER.info("Added %d new Matter Time Sync buttons.", len(new_entities))

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
        self._coordinator = coordinator
        self._node_id = node_id
        self._node_name = node_name

        self._press_lock = asyncio.Lock()
        self._last_press_ts: float = 0.0

        name_slug = slugify(node_name)
        self._attr_unique_id = f"matter_time_sync_{node_id}"
        self._attr_name = f"{node_name} Sync Time"

        # NOTE: setting entity_id directly is generally discouraged, but keeping
        # the original behavior for now.
        self.entity_id = f"button.{name_slug}_sync_time"

        # Attach to existing Matter device (preferred). If we can't find it,
        # do NOT create a new device.
        if matter_identifiers:
            self._attr_device_info = DeviceInfo(identifiers=matter_identifiers)
        else:
            self._attr_device_info = None

        self._vendor_name = None
        self._product_name = None
        if device_info:
            self._vendor_name = device_info.get("vendor_name")
            self._product_name = device_info.get("product_name") or device_info.get("product_name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "node_id": self._node_id,
            "device_name": self._node_name,
            "integration": DOMAIN,
            "vendor_name": self._vendor_name,
            "product_name": self._product_name,
        }

    async def async_press(self) -> None:
        now = time.monotonic()
        if (now - self._last_press_ts) < self._PRESS_COOLDOWN_SECONDS:
            return

        async with self._press_lock:
            now = time.monotonic()
            if (now - self._last_press_ts) < self._PRESS_COOLDOWN_SECONDS:
                return

            self._last_press_ts = now

            _LOGGER.info("Syncing time for Matter node %s (%s)", self._node_id, self._node_name)
            success = await self._coordinator.async_sync_time(self._node_id, endpoint=0)
            if success:
                _LOGGER.info("Time sync successful for %s (node %s)", self._node_name, self._node_id)
            else:
                _LOGGER.error("Time sync failed for %s (node %s)", self._node_name, self._node_id)
