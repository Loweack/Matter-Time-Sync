"""Button platform for Matter Time Sync.

Attaches per-device Sync Time buttons to the existing Matter devices in HA
(so it does NOT create extra devices).

Adds a configurable filter target:
- any: match filter against display name + node label + product name
- display_name: match only the resolved display name (node['name'])
- ha_name: match only if name_source == 'home_assistant'
- matter: match only node label + product name (and also the resolved name if it comes from those sources)
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

CONF_FILTER_TARGET = "filter_target"
DEFAULT_FILTER_TARGET = "any"  # any | display_name | ha_name | matter

_MATTER_ID_RE = re.compile(r"deviceid_[0-9A-Fa-f]+-([0-9A-Fa-f]{16})-MatterNodeDevice")


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    return text[:50] if len(text) > 50 else text


def _normalize_terms(filters: list[str]) -> list[str]:
    return [t.strip().lower() for t in filters if t and t.strip()]


def _matches_any_candidate(filters: list[str], candidates: list[str]) -> bool:
    terms = _normalize_terms(filters)
    if not terms:
        return True
    haystacks = [c.lower() for c in candidates if c]
    return any(term in h for term in terms for h in haystacks)


def _filter_candidates_for_node(node: dict[str, Any], filter_target: str) -> list[str]:
    node_name = node.get("name") or ""
    name_source = node.get("name_source") or ""
    product_name = node.get("product_name") or ""
    node_label = (node.get("device_info") or {}).get("node_label", "") or ""

    if filter_target == "display_name":
        return [node_name]

    if filter_target == "ha_name":
        return [node_name] if name_source == "home_assistant" else []

    if filter_target == "matter":
        candidates = [product_name, node_label]
        if name_source in ("node_label", "product_name"):
            candidates.append(node_name)
        return candidates

    # default: any
    return [node_name, product_name, node_label]


def _matter_device_identifiers_for_node(hass: HomeAssistant, node_id: int) -> set[tuple[str, str]] | None:
    device_reg = dr.async_get(hass)
    needle = f"-{node_id:016X}-MatterNodeDevice"

    for dev in device_reg.devices.values():
        for domain, ident in dev.identifiers:
            if domain != "matter":
                continue
            ident_str = str(ident)

            if needle in ident_str:
                return {(domain, ident_str)}

            m = _MATTER_ID_RE.search(ident_str)
            if m:
                try:
                    parsed_node_id = int(m.group(1), 16)
                except ValueError:
                    continue
                if parsed_node_id == node_id:
                    return {(domain, ident_str)}

    return None


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]
    device_filters = entry_data.get("device_filters", [])
    only_time_sync = entry_data.get("only_time_sync_devices", True)
    filter_target = entry_data.get(CONF_FILTER_TARGET, DEFAULT_FILTER_TARGET)

    entry_data["async_add_entities"] = async_add_entities

    nodes = await coordinator.async_get_matter_nodes()

    _LOGGER.info(
        "Matter Time Sync: Found %d nodes from Matter Server, filter=%s, filter_target=%s, only_time_sync=%s",
        len(nodes),
        device_filters if device_filters else "[empty - all devices]",
        filter_target,
        only_time_sync,
    )

    entities: list[MatterTimeSyncButton] = []
    known_node_ids: set[int] = set()

    for node in nodes:
        node_id = node.get("node_id")
        if node_id is None:
            continue

        if only_time_sync and not node.get("has_time_sync", False):
            continue

        candidates = _filter_candidates_for_node(node, filter_target)
        if not _matches_any_candidate(device_filters, candidates):
            continue

        known_node_ids.add(node_id)
        matter_identifiers = _matter_device_identifiers_for_node(hass, node_id)

        entities.append(
            MatterTimeSyncButton(
                coordinator=coordinator,
                node_id=node_id,
                node_name=node.get("name") or f"Matter Node {node_id}",
                device_info=node.get("device_info"),
                matter_identifiers=matter_identifiers,
            )
        )

    if entities:
        async_add_entities(entities)
        _LOGGER.info("Added %d Matter Time Sync buttons.", len(entities))
    else:
        _LOGGER.warning("No Matter Time Sync buttons created (filtered out or no Time Sync support)." )

    entry_data["known_node_ids"] = known_node_ids


async def async_check_new_devices(hass: HomeAssistant, entry_id: str) -> int:
    entry_data = hass.data[DOMAIN].get(entry_id)
    if not entry_data:
        return 0

    coordinator = entry_data["coordinator"]
    device_filters = entry_data.get("device_filters", [])
    only_time_sync = entry_data.get("only_time_sync_devices", True)
    filter_target = entry_data.get(CONF_FILTER_TARGET, DEFAULT_FILTER_TARGET)

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

        if only_time_sync and not node.get("has_time_sync", False):
            continue

        candidates = _filter_candidates_for_node(node, filter_target)
        if not _matches_any_candidate(device_filters, candidates):
            continue

        known_node_ids.add(node_id)
        matter_identifiers = _matter_device_identifiers_for_node(hass, node_id)

        new_entities.append(
            MatterTimeSyncButton(
                coordinator=coordinator,
                node_id=node_id,
                node_name=node.get("name") or f"Matter Node {node_id}",
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
        self.entity_id = f"button.{name_slug}_sync_time"

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
