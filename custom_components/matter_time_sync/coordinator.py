"""Coordinator for Matter Time Sync."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import WSMsgType
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_WS_URL,
    CONF_TIMEZONE,
    TIME_SYNC_CLUSTER_ID,
    DEFAULT_TIMEZONE,
)

_LOGGER = logging.getLogger(__name__)


class MatterTimeSyncCoordinator:
    """Coordinator to manage Matter Server WebSocket connection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self._ws_url = entry.data.get(CONF_WS_URL, "ws://localhost:5580/ws")
        self._timezone = entry.data.get(CONF_TIMEZONE, DEFAULT_TIMEZONE)
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._message_id = 0
        self._nodes_cache: list[dict[str, Any]] = []
        self._connected = False
        self._lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()  # Prevent concurrent WS reads

        # Per-node lock: prevents multiple sync runs for the same node_id from interleaving
        self._per_node_sync_locks: dict[int, asyncio.Lock] = {}

    @property
    def is_connected(self) -> bool:
        """Return True if connected to Matter Server."""
        return self._connected and self._ws is not None and not self._ws.closed

    async def async_connect(self) -> bool:
        """Connect to Matter Server WebSocket."""
        async with self._lock:
            if self.is_connected:
                return True

            try:
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(
                    self._ws_url, timeout=aiohttp.ClientTimeout(total=10)
                )
                self._connected = True
                _LOGGER.info("Connected to Matter Server at %s", self._ws_url)
                return True
            except Exception as err:
                _LOGGER.error("Failed to connect to Matter Server: %s", err)
                await self._cleanup_connection()
                return False

    async def _cleanup_connection(self) -> None:
        """Close and cleanup websocket + session."""
        self._connected = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    async def async_disconnect(self) -> None:
        """Disconnect from Matter Server."""
        async with self._lock:
            await self._cleanup_connection()

    async def _async_send_command(
        self, command: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Send a command to the Matter Server and wait for response."""
        # Only allow one in-flight command at a time.
        # Without this, multiple coroutines would read from the same websocket
        # and responses could be delivered to the wrong caller.
        async with self._command_lock:
            if not self.is_connected:
                if not await self.async_connect():
                    return None

            self._message_id += 1
            message_id = str(self._message_id)

            request = {
                "message_id": message_id,
                "command": command,
            }
            if args:
                request["args"] = args

            try:
                await self._ws.send_json(request)

                # Wait for response with matching message_id
                async def _wait_for_response():
                    async for msg in self._ws:
                        if msg.type == WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("message_id") == message_id:
                                if "error_code" in data:
                                    # Log warning instead of error to avoid noise during fallbacks
                                    _LOGGER.debug(
                                        "Matter Server error for command %s: %s",
                                        command,
                                        data.get("details", "Unknown error"),
                                    )
                                    return None
                                return data
                        elif msg.type == WSMsgType.ERROR:
                            _LOGGER.error("WebSocket error: %s", msg.data)
                            return None
                        elif msg.type == WSMsgType.CLOSED:
                            _LOGGER.warning("WebSocket closed unexpectedly")
                            await self._cleanup_connection()
                            return None

                # IMPORTANT: timeout prevents deadlock if server never responds
                return await asyncio.wait_for(_wait_for_response(), timeout=10)

            except asyncio.TimeoutError:
                _LOGGER.error("Timeout waiting for response to %s", command)
                return None
            except Exception as err:
                _LOGGER.error("Error sending command to Matter Server: %s", err)
                await self._cleanup_connection()
                return None

    def _get_ha_device_name(self, node_id: int) -> str | None:
        """
        Try to get the device name from Home Assistant's device registry.
        This gets the user-defined name if the device was renamed in HA.
        """
        try:
            device_reg = dr.async_get(self.hass)
            # Search for device with matter identifier
            for device in device_reg.devices.values():
                for identifier in device.identifiers:
                    # Matter integration uses ("matter", "deviceid_X") format
                    if identifier[0] == "matter":
                        # Check if the node_id matches
                        # Matter uses format like "deviceid_1" or just the node_id
                        id_str = str(identifier[1])
                        if (
                            id_str == str(node_id)
                            or id_str == f"deviceid_{node_id}"
                            or id_str.endswith(f"_{node_id}")
                        ):
                            # Return user-defined name if set, otherwise original name
                            if device.name_by_user:
                                _LOGGER.debug(
                                    "Found HA device name for node %s: %s (user-defined)",
                                    node_id,
                                    device.name_by_user,
                                )
                                return device.name_by_user
                            elif device.name:
                                _LOGGER.debug(
                                    "Found HA device name for node %s: %s",
                                    node_id,
                                    device.name,
                                )
                                return device.name
        except Exception as err:
            _LOGGER.debug("Could not get HA device name: %s", err)
        return None

    async def async_get_matter_nodes(self) -> list[dict[str, Any]]:
        """Get all Matter nodes from the server."""
        response = await self._async_send_command("get_nodes")
        if not response:
            return self._nodes_cache  # Return cached data if available

        raw_nodes = response.get("result", [])
        self._nodes_cache = self._parse_nodes(raw_nodes)
        return self._nodes_cache

    def _parse_nodes(self, raw_nodes: list) -> list[dict[str, Any]]:
        """Parse raw node data into usable format."""
        parsed = []
        for node in raw_nodes:
            node_id = node.get("node_id")
            if node_id is None:
                continue

            attributes = node.get("attributes", {})

            # Matter Server uses format: "endpoint/cluster/attribute"
            # Basic Information Cluster = 40 (0x28)
            # Attribute IDs: 1=VendorName, 3=ProductName, 5=NodeLabel, 15=SerialNumber
            device_info = {
                "vendor_name": attributes.get("0/40/1", "Unknown"),
                "product_name": attributes.get("0/40/3", ""),
                "node_label": attributes.get("0/40/5", ""),
                "serial_number": attributes.get("0/40/15", ""),
            }

            # Check for Time Sync Cluster (0x38 = 56)
            # If any attribute from cluster 56 exists, the device supports it
            has_time_sync = self._check_time_sync_cluster(attributes)

            # Determine the best name to use (priority order):
            # 1. Home Assistant user-defined name (from device registry)
            # 2. Matter NodeLabel (if user set it on the device)
            # 3. Matter ProductName (e.g. "ALPSTUGA air quality monitor")
            # 4. Fallback with node_id
            ha_name = self._get_ha_device_name(node_id)
            node_label = device_info.get("node_label", "")
            product_name = device_info.get("product_name", "")

            if ha_name:
                name = ha_name
                name_source = "home_assistant"
            elif node_label:
                name = node_label
                name_source = "node_label"
            elif product_name:
                name = product_name
                name_source = "product_name"
            else:
                name = f"Matter Node {node_id}"
                name_source = "fallback"

            _LOGGER.info(
                "Node %s: name='%s' (source: %s), product='%s', has_time_sync=%s",
                node_id,
                name,
                name_source,
                product_name,
                has_time_sync,
            )

            parsed.append(
                {
                    "node_id": node_id,
                    "name": name,
                    "name_source": name_source,
                    "product_name": product_name,
                    "device_info": device_info,
                    "has_time_sync": has_time_sync,
                }
            )

        _LOGGER.info("Parsed %d Matter nodes", len(parsed))
        return parsed

    def _check_time_sync_cluster(self, attributes: dict) -> bool:
        """Check if node has Time Synchronization cluster."""
        # Time Sync Cluster is 0x0038 (56 decimal)
        # Check if ANY attribute from cluster 56 exists
        for key in attributes.keys():
            # Key format: "endpoint/cluster/attribute"
            parts = key.split("/")
            if len(parts) >= 2:
                try:
                    cluster = int(parts[1])
                    if cluster == 56:  # Time Sync cluster
                        return True
                except ValueError:
                    continue
        return False

    async def async_sync_time(self, node_id: int, endpoint: int = 0) -> bool:
        """Sync time on a Matter device."""
        lock = self._per_node_sync_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            try:
                tz = ZoneInfo(self._timezone)
            except Exception:
                _LOGGER.warning("Invalid timezone %s, using UTC", self._timezone)
                tz = ZoneInfo("UTC")

            now = datetime.now(tz)
            utc_now = now.astimezone(ZoneInfo("UTC"))

            # Total UTC offset in seconds (includes DST when applicable)
            total_offset = int(now.utcoffset().total_seconds()) if now.utcoffset() else 0

            # FORCE DST TO 0
            # We merge the DST offset into the main timezone offset.
            # This prevents "double counting" on devices that support DST,
            # and prevents "lag" on devices that ignore the DST command.
            utc_offset = total_offset
            dst_offset = 0

            # UTC time in microseconds since epoch
            utc_microseconds = int(utc_now.timestamp() * 1_000_000)

            _LOGGER.info(
                "Syncing time for node %s: local=%s, UTC=%s, offset=%ds, DST=%ds (forced to 0)",
                node_id,
                now.isoformat(),
                utc_now.isoformat(),
                utc_offset,
                dst_offset,
            )

            # ---------------------------------------------------------
            # 1. Set UTC Time (Try PascalCase first, then camelCase)
            # ---------------------------------------------------------

            # Payload variants
            payload_utc_pascal = {
                "UTCTime": utc_microseconds,
                "granularity": 3,
            }
            payload_utc_camel = {
                "utcTime": utc_microseconds,
                "granularity": 3,
            }

            # Attempt 1: Try PascalCase (Old Standard)
            time_response = await self._async_send_command(
                "device_command",
                {
                    "node_id": node_id,
                    "endpoint_id": endpoint,
                    "cluster_id": TIME_SYNC_CLUSTER_ID,
                    "command_name": "SetUTCTime",
                    "payload": payload_utc_pascal,
                },
            )

            # Attempt 2: If failed, try camelCase (New Standard)
            if not time_response:
                _LOGGER.debug("SetUTCTime (PascalCase) failed, trying camelCase...")
                time_response = await self._async_send_command(
                    "device_command",
                    {
                        "node_id": node_id,
                        "endpoint_id": endpoint,
                        "cluster_id": TIME_SYNC_CLUSTER_ID,
                        "command_name": "SetUTCTime",
                        "payload": payload_utc_camel,
                    },
                )

            if not time_response:
                _LOGGER.error(
                    "Failed to set UTC time for node %s (tried both formats)", node_id
                )
                return False

            _LOGGER.debug("SetUTCTime successful for node %s", node_id)

            # DELAY 1: Allow device to process UTC time update
            await asyncio.sleep(1.0)

            # ---------------------------------------------------------
            # 2. Set Timezone
            # ---------------------------------------------------------

            # 'timeZone' is commonly camelCase.
            # We send the FULL offset (standard + DST) here.
            tz_payload_list = [
                {
                    "offset": utc_offset,
                    "validAt": 0,
                    "name": self._timezone,
                }
            ]

            tz_response = await self._async_send_command(
                "device_command",
                {
                    "node_id": node_id,
                    "endpoint_id": endpoint,
                    "cluster_id": TIME_SYNC_CLUSTER_ID,
                    "command_name": "SetTimeZone",
                    "payload": {"timeZone": tz_payload_list},
                },
            )

            if tz_response:
                _LOGGER.debug(
                    "SetTimeZone successful for node %s (offset=%d)", node_id, utc_offset
                )
            else:
                _LOGGER.warning(
                    "SetTimeZone failed for node %s, trying without name", node_id
                )
                # Try without name (some devices don't support it)
                tz_payload_list_no_name = [
                    {
                        "offset": utc_offset,
                        "validAt": 0,
                    }
                ]
                tz_response = await self._async_send_command(
                    "device_command",
                    {
                        "node_id": node_id,
                        "endpoint_id": endpoint,
                        "cluster_id": TIME_SYNC_CLUSTER_ID,
                        "command_name": "SetTimeZone",
                        "payload": {"timeZone": tz_payload_list_no_name},
                    },
                )
                if tz_response:
                    _LOGGER.debug(
                        "SetTimeZone (without name) successful for node %s", node_id
                    )
                else:
                    _LOGGER.warning("SetTimeZone completely failed for node %s", node_id)

            # DELAY 2: Allow device to process TimeZone update
            await asyncio.sleep(1.0)

            # ---------------------------------------------------------
            # 3. Set DST Offset (Try PascalCase first, then camelCase)
            # ---------------------------------------------------------

            # Use a far-future timestamp for validUntil instead of 0
            far_future_us = int(
                (utc_now.timestamp() + 365 * 24 * 3600) * 1_000_000
            )  # 1 year from now

            dst_list = [
                {
                    "offset": dst_offset,  # Always 0
                    "validStarting": 0,
                    "validUntil": far_future_us,
                }
            ]

            payload_dst_pascal = {"DSTOffset": dst_list}
            payload_dst_camel = {"dstOffset": dst_list}

            # Attempt 1: Try PascalCase
            dst_response = await self._async_send_command(
                "device_command",
                {
                    "node_id": node_id,
                    "endpoint_id": endpoint,
                    "cluster_id": TIME_SYNC_CLUSTER_ID,
                    "command_name": "SetDSTOffset",
                    "payload": payload_dst_pascal,
                },
            )

            # Attempt 2: If failed, try camelCase
            if not dst_response:
                _LOGGER.debug("SetDSTOffset (PascalCase) failed, trying camelCase...")
                dst_response = await self._async_send_command(
                    "device_command",
                    {
                        "node_id": node_id,
                        "endpoint_id": endpoint,
                        "cluster_id": TIME_SYNC_CLUSTER_ID,
                        "command_name": "SetDSTOffset",
                        "payload": payload_dst_camel,
                    },
                )

            if dst_response:
                _LOGGER.debug("SetDSTOffset (0) successful for node %s", node_id)
            else:
                _LOGGER.debug(
                    "SetDSTOffset not supported or failed for node %s (this is often OK)",
                    node_id,
                )

            _LOGGER.info(
                "Time synced for node %s: %s (UTC offset: %d, DST: %d)",
                node_id,
                now.isoformat(),
                utc_offset,
                dst_offset,
            )
            return True

    async def async_sync_all_devices(self) -> None:
        """Sync time on all filtered devices."""
        # Get latest nodes
        nodes = await self.async_get_matter_nodes()

        # Get filters from config
        device_filters = self.entry.data.get("device_filter", "")
        device_filters = [
            t.strip().lower()
            for t in device_filters.split(",")
            if t.strip()
        ]
        only_time_sync = self.entry.data.get("only_time_sync_devices", True)

        count = 0
        for node in nodes:
            node_id = node.get("node_id")
            node_name = node.get("name", f"Node {node_id}")
            has_time_sync = node.get("has_time_sync", False)

            # Apply Logic: Skip if only_time_sync is True AND device doesn't support it
            if only_time_sync and not has_time_sync:
                continue

            # Apply Filter Logic
            # (Matches simpler logic often found in HA integrations)
            if device_filters:
                if not any(term in node_name.lower() for term in device_filters):
                    continue

            # Sync!
            _LOGGER.info("Auto-syncing node %s", node_id)
            await self.async_sync_time(node_id)
            count += 1

        _LOGGER.info("Sync all completed. Synced %d devices.", count)

    def update_config(self, ws_url: str, timezone: str) -> None:
        """Update configuration (called when options change)."""
        self._ws_url = ws_url
        self._timezone = timezone
        _LOGGER.debug("Configuration updated: URL=%s, TZ=%s", ws_url, timezone)
