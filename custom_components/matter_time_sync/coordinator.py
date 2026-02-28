"""Coordinator for Matter Time Sync."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import WSMsgType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_FILTER_TARGET,
    CONF_TIMEZONE,
    CONF_WS_URL,
    DEFAULT_FILTER_TARGET,
    DEFAULT_TIMEZONE,
    DEFAULT_WS_URL,
    TIME_SYNC_CLUSTER_ID,
)

_LOGGER = logging.getLogger(__name__)

# Matter/CHIP epoch used by Time Synchronization cluster (microseconds since 2000-01-01)
_CHIP_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _to_chip_epoch_us(dt: datetime) -> int:
    """Convert a datetime to microseconds since CHIP epoch (2000-01-01)."""
    dt_utc = dt.astimezone(timezone.utc)
    return int((dt_utc - _CHIP_EPOCH).total_seconds() * 1_000_000)


def _pascal_to_camel(key: str) -> str:
    """Convert a PascalCase key to camelCase, with acronym-aware lowering.

    Examples:
        'TimeZone'      -> 'timeZone'
        'ValidAt'       -> 'validAt'
        'Granularity'   -> 'granularity'
        'Offset'        -> 'offset'
        'UTCTime'       -> 'utcTime'
        'DSTOffset'     -> 'dstOffset'
        'ValidStarting' -> 'validStarting'
        'ValidUntil'    -> 'validUntil'
    """
    if not key or not key[0].isupper():
        return key

    # Find where the leading uppercase run ends.
    upper_run = 0
    for ch in key:
        if ch.isupper():
            upper_run += 1
        else:
            break

    if upper_run <= 1:
        # Simple PascalCase: "TimeZone" -> "timeZone"
        return key[0].lower() + key[1:]

    if upper_run == len(key):
        # Entire key is uppercase: "DST" -> "dst"
        return key.lower()

    # Acronym followed by PascalCase word: "DSTOffset" -> "dstOffset"
    return key[: upper_run - 1].lower() + key[upper_run - 1 :]


def _convert_keys_to_camel(payload: Any) -> Any:
    """Recursively convert all dict keys in a payload from PascalCase to camelCase."""
    if isinstance(payload, dict):
        return {_pascal_to_camel(k): _convert_keys_to_camel(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_convert_keys_to_camel(item) for item in payload]
    return payload


# ------------------------------------------------------------------
# Filter helpers (shared with button.py)
# ------------------------------------------------------------------


def filter_candidates_for_node(
    node: dict[str, Any], filter_target: str
) -> list[str]:
    """Return the list of strings to match the device filter against.

    Supports filter_target values:
    - any: match filter against display name + node label + product name
    - display_name: match only the resolved display name (node['name'])
    - ha_name: match only if name_source == 'home_assistant'
    - matter: match only node label + product name
    """
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


def device_matches_filter(
    filters: list[str], candidates: list[str]
) -> bool:
    """Check if any candidate string matches any of the filter terms.

    Uses case-insensitive partial matching.
    If filters is empty, all devices match.
    Expects filters to already be stripped and lowercased.
    """
    if not filters:
        return True

    haystacks = [c.lower() for c in candidates if c]
    return any(term in h for term in filters for h in haystacks)


class MatterTimeSyncCoordinator:
    """Coordinator to manage Matter Server WebSocket connection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self._ws_url = entry.data.get(CONF_WS_URL, DEFAULT_WS_URL)
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

        # Auto-sync state tracking
        self._auto_sync_running = False
        self._auto_sync_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Return True if connected to Matter Server."""
        return self._connected and self._ws is not None and not self._ws.closed

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def async_connect(self) -> bool:
        """Connect to Matter Server WebSocket."""
        async with self._lock:
            if self.is_connected:
                return True

            # Reset state before connecting
            self._connected = False
            self._ws = None
            self._session = None

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
                self._connected = False
                if self._ws:
                    await self._ws.close()
                    self._ws = None
                if self._session:
                    await self._session.close()
                    self._session = None
                return False

    async def _cleanup_connection(self) -> None:
        """Close and cleanup websocket + session.

        Acquires _lock internally — safe to call from any context EXCEPT
        while holding _command_lock (to avoid lock-ordering inversion).
        """
        async with self._lock:
            self._connected = False
            ws, self._ws = self._ws, None
            session, self._session = self._session, None

        # Close outside the lock to avoid holding it during I/O
        if ws:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if session:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass

    async def async_disconnect(self) -> None:
        """Disconnect from Matter Server."""
        await self._cleanup_connection()

    # ------------------------------------------------------------------
    # WebSocket command handling
    # ------------------------------------------------------------------

    async def _async_send_command(
        self, command: str, args: dict[str, Any] | None = None, retry: bool = True
    ) -> dict[str, Any] | None:
        """Send a command to the Matter Server and wait for response.

        The actual send/receive is performed inside _do_send_command while
        holding _command_lock.  If the connection turns out to be broken we
        release the lock, reconnect, and retry once — avoiding a recursive
        call that could race on _message_id.

        _cleanup_connection is ONLY called outside _command_lock to prevent
        a lock-ordering inversion (_command_lock -> _lock vs _lock -> _command_lock).
        """
        async with self._command_lock:
            result, should_retry = await self._do_send_command(command, args)

        # Handle retry outside _command_lock
        if result is None and should_retry and retry:
            _LOGGER.warning(
                "WebSocket connection lost, reconnecting and retrying command %s",
                command,
            )
            # Cleanup OUTSIDE _command_lock — safe lock ordering
            await self._cleanup_connection()
            if await self.async_connect():
                async with self._command_lock:
                    result, _ = await self._do_send_command(command, args)
            return result

        # Clean up on non-retryable connection failures (outside _command_lock)
        if result is None and not self._connected:
            await self._cleanup_connection()

        return result

    async def _async_send_device_command(
        self,
        node_id: int,
        endpoint_id: int,
        cluster_id: int,
        command_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Send a device_command with PascalCase payload, falling back to camelCase.

        This method:
        1. Sends the command with the PascalCase payload as provided.
        2. If the server returns an error, converts keys to camelCase and retries.
        3. Returns the successful response, or None if both attempts fail.
        """
        _LOGGER.debug(
            "Sending %s to node %s with PascalCase payload: %s",
            command_name,
            node_id,
            payload,
        )

        response = await self._async_send_command(
            "device_command",
            {
                "node_id": node_id,
                "endpoint_id": endpoint_id,
                "cluster_id": cluster_id,
                "command_name": command_name,
                "payload": payload,
            },
        )

        if response is not None:
            return response

        # Fallback: retry with camelCase keys
        camel_payload = _convert_keys_to_camel(payload)
        if camel_payload == payload:
            # Keys are identical — no point retrying
            return None

        _LOGGER.info(
            "PascalCase payload rejected for %s on node %s, retrying with camelCase: %s",
            command_name,
            node_id,
            camel_payload,
        )

        return await self._async_send_command(
            "device_command",
            {
                "node_id": node_id,
                "endpoint_id": endpoint_id,
                "cluster_id": cluster_id,
                "command_name": command_name,
                "payload": camel_payload,
            },
        )

    async def _do_send_command(
        self, command: str, args: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, bool]:
        """Send a command and wait for its response.

        Returns (response, should_retry).
        Must be called while holding _command_lock.

        IMPORTANT: This method must NEVER call _cleanup_connection() because
        that acquires _lock and we already hold _command_lock — doing so would
        create a lock-ordering inversion.  Instead we just set
        self._connected = False and let the caller handle cleanup.
        """
        if not self.is_connected:
            if not await self.async_connect():
                return None, False

        self._message_id += 1
        message_id = str(self._message_id)

        request: dict[str, Any] = {
            "message_id": message_id,
            "command": command,
        }
        if args:
            request["args"] = args

        try:
            await self._ws.send_json(request)

            async def _wait_for_response() -> dict[str, Any] | None:
                async for msg in self._ws:
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("message_id") == message_id:
                            if "error_code" in data:
                                _LOGGER.warning(
                                    "Matter Server error for command %s: [%s] %s",
                                    command,
                                    data.get("error_code"),
                                    data.get("details", "Unknown error"),
                                )
                                return None
                            return data
                        # Unsolicited / mismatched message — log and skip
                        _LOGGER.debug(
                            "Ignoring unsolicited message (id=%s)",
                            data.get("message_id"),
                        )
                    elif msg.type == WSMsgType.ERROR:
                        _LOGGER.error("WebSocket error: %s", msg.data)
                        return None
                    elif msg.type == WSMsgType.CLOSED:
                        _LOGGER.warning("WebSocket closed unexpectedly")
                        self._connected = False
                        return None
                return None

            response = await asyncio.wait_for(_wait_for_response(), timeout=10)
            return response, False

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout waiting for response to %s", command)
            return None, False
        except Exception as err:
            err_str = str(err).lower()
            if "closing" in err_str or "closed" in err_str:
                self._connected = False
                return None, True  # Signal caller to retry

            _LOGGER.error("Error sending command to Matter Server: %s", err)
            self._connected = False
            return None, False

    # ------------------------------------------------------------------
    # Device name resolution
    # ------------------------------------------------------------------

    def _get_ha_device_name(self, node_id: int) -> str | None:
        """Try to get the device name from Home Assistant's device registry."""
        try:
            device_reg = dr.async_get(self.hass)
            node_id_str = str(node_id)
            for device in device_reg.devices.values():
                for identifier in device.identifiers:
                    if identifier[0] != "matter":
                        continue

                    id_str = str(identifier[1])
                    if (
                        id_str == node_id_str
                        or id_str == f"deviceid_{node_id_str}"
                        or id_str.rsplit("_", 1)[-1] == node_id_str
                    ):
                        if device.name_by_user:
                            _LOGGER.debug(
                                "Found HA device name for node %s: %s (user-defined)",
                                node_id,
                                device.name_by_user,
                            )
                            return device.name_by_user
                        if device.name:
                            _LOGGER.debug(
                                "Found HA device name for node %s: %s",
                                node_id,
                                device.name,
                            )
                            return device.name
        except Exception as err:
            _LOGGER.debug("Could not get HA device name: %s", err)
        return None

    # ------------------------------------------------------------------
    # Node discovery / parsing
    # ------------------------------------------------------------------

    async def async_get_matter_nodes(self) -> list[dict[str, Any]]:
        """Get all Matter nodes from the server."""
        response = await self._async_send_command("get_nodes")
        if not response:
            return self._nodes_cache

        raw_nodes = response.get("result", [])
        self._nodes_cache = self._parse_nodes(raw_nodes)

        # Clean up locks for nodes that no longer exist
        current_node_ids = {n["node_id"] for n in self._nodes_cache}
        stale_ids = set(self._per_node_sync_locks.keys()) - current_node_ids
        for nid in stale_ids:
            lock = self._per_node_sync_locks.get(nid)
            if lock and lock.locked():
                continue
            self._per_node_sync_locks.pop(nid, None)
            _LOGGER.debug("Removed stale sync lock for node %s", nid)

        return self._nodes_cache

    def _get_time_sync_endpoints(self, attributes: dict[str, Any]) -> list[int]:
        """Return endpoint(s) that expose the Time Synchronization cluster (56)."""
        endpoints: set[int] = set()
        for key in attributes:
            parts = key.split("/")
            if len(parts) < 2:
                continue
            try:
                endpoint_id = int(parts[0])
                cluster_id = int(parts[1])
            except ValueError:
                continue
            if cluster_id == 56:
                endpoints.add(endpoint_id)
        return sorted(endpoints)

    def _parse_nodes(self, raw_nodes: list) -> list[dict[str, Any]]:
        """Parse raw node data into usable format."""
        parsed: list[dict[str, Any]] = []
        for node in raw_nodes:
            node_id = node.get("node_id")
            if node_id is None:
                continue

            attributes = node.get("attributes", {})

            device_info = {
                "vendor_name": attributes.get("0/40/1", "Unknown"),
                "product_name": attributes.get("0/40/3", ""),
                "node_label": attributes.get("0/40/5", ""),
                "serial_number": attributes.get("0/40/15", ""),
            }

            time_sync_endpoints = self._get_time_sync_endpoints(attributes)
            has_time_sync = bool(time_sync_endpoints)

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
                    "time_sync_endpoints": time_sync_endpoints,
                }
            )

        _LOGGER.info("Parsed %d Matter nodes", len(parsed))
        return parsed

    async def async_get_time_sync_cluster_info(
        self, node_id: int, endpoint_id: int
    ) -> dict[str, Any]:
        """Get Time Sync cluster information for diagnostics."""
        response = await self._async_send_command("get_nodes")
        if not response:
            return {}

        raw_nodes = response.get("result", [])
        node = next((n for n in raw_nodes if n.get("node_id") == node_id), None)
        if not node:
            return {}

        attributes = node.get("attributes", {})
        time_sync_attrs = {}

        for key, value in attributes.items():
            parts = key.split("/")
            if len(parts) >= 2:
                try:
                    ep_id = int(parts[0])
                    cluster_id = int(parts[1])
                    if ep_id == endpoint_id and cluster_id == 56:
                        time_sync_attrs[key] = value
                except ValueError:
                    continue

        return time_sync_attrs

    # ------------------------------------------------------------------
    # Time synchronisation
    # ------------------------------------------------------------------

    async def async_sync_time(self, node_id: int, endpoint: int | None = None) -> bool:
        """Sync time on a Matter device.

        Pass endpoint=None to auto-detect the correct endpoint.
        """
        lock = self._per_node_sync_locks.setdefault(node_id, asyncio.Lock())

        async def _acquire_and_sync() -> bool:
            async with lock:
                return await self._do_sync_time(node_id, endpoint)

        try:
            return await asyncio.wait_for(_acquire_and_sync(), timeout=20)
        except asyncio.TimeoutError:
            _LOGGER.error(
                "Timeout syncing node %s (exceeded 20s)",
                node_id,
            )
            return False

    async def _do_sync_time(self, node_id: int, endpoint: int | None = None) -> bool:
        """Internal method to perform time sync (called within lock)."""
        _LOGGER.debug("Starting time sync for node %s (endpoint %s)", node_id, endpoint)

        # Ensure we have node info for endpoint auto-selection
        if not self._nodes_cache:
            await self.async_get_matter_nodes()

        endpoint_id = endpoint
        if endpoint_id is None:
            node = next(
                (n for n in self._nodes_cache if n.get("node_id") == node_id),
                None,
            )
            endpoints = (node or {}).get("time_sync_endpoints") or []
            if endpoints:
                endpoint_id = endpoints[0]
            else:
                endpoint_id = 0  # Fallback when no endpoints are known

            _LOGGER.debug(
                "Auto-detected Time Sync endpoint %s for node %s",
                endpoint_id,
                node_id,
            )

            # Log Time Sync cluster attributes for diagnostics (optional, with timeout)
            try:
                time_sync_attrs = await asyncio.wait_for(
                    self.async_get_time_sync_cluster_info(node_id, endpoint_id),
                    timeout=5,
                )
                if time_sync_attrs:
                    _LOGGER.debug(
                        "Node %s endpoint %s Time Sync cluster attributes: %s",
                        node_id,
                        endpoint_id,
                        time_sync_attrs,
                    )
                else:
                    _LOGGER.debug(
                        "Node %s endpoint %s: No Time Sync cluster attributes found",
                        node_id,
                        endpoint_id,
                    )
            except asyncio.TimeoutError:
                _LOGGER.debug(
                    "Timeout getting Time Sync attributes for node %s (non-critical)",
                    node_id,
                )
            except Exception as err:
                _LOGGER.debug(
                    "Could not get Time Sync attributes for node %s: %s (non-critical)",
                    node_id,
                    err,
                )

        try:
            tz = ZoneInfo(self._timezone)
        except Exception:
            _LOGGER.warning("Invalid timezone %s, using UTC", self._timezone)
            tz = ZoneInfo("UTC")

        now = datetime.now(tz)
        utc_now = now.astimezone(ZoneInfo("UTC"))

        # Total UTC offset in seconds (includes DST when applicable)
        total_offset = int(now.utcoffset().total_seconds()) if now.utcoffset() else 0

        # FORCE DST TO 0 (merge DST into utc_offset)
        utc_offset = total_offset
        dst_offset = 0

        # Matter Time Sync uses CHIP epoch (2000-01-01) in microseconds
        utc_microseconds = _to_chip_epoch_us(utc_now)

        _LOGGER.info(
            "Syncing time for node %s: local=%s, UTC=%s, offset=%ds, DST=%ds (forced to 0)",
            node_id,
            now.isoformat(),
            utc_now.isoformat(),
            utc_offset,
            dst_offset,
        )

        # ---------------------------------------------------------
        # 1) Set TimeZone FIRST
        #    PascalCase primary: {"TimeZone": [{"Offset": ..., "ValidAt": ...}]}
        #    camelCase fallback: {"timeZone": [{"offset": ..., "validAt": ...}]}
        # ---------------------------------------------------------
        tz_list = [{"Offset": utc_offset, "ValidAt": 0}]

        tz_response = await self._async_send_device_command(
            node_id=node_id,
            endpoint_id=endpoint_id,
            cluster_id=TIME_SYNC_CLUSTER_ID,
            command_name="SetTimeZone",
            payload={"TimeZone": tz_list},
        )

        if tz_response:
            _LOGGER.debug(
                "SetTimeZone successful for node %s (offset=%d)",
                node_id,
                utc_offset,
            )
        else:
            _LOGGER.warning(
                "SetTimeZone failed for node %s (continuing anyway)", node_id
            )

        # ---------------------------------------------------------
        # 2) Set DST Offset SECOND
        #    PascalCase primary: {"DSTOffset": [{"Offset": ..., "ValidStarting": ..., "ValidUntil": ...}]}
        #    camelCase fallback: {"dstOffset": [{"offset": ..., "validStarting": ..., "validUntil": ...}]}
        # ---------------------------------------------------------
        far_future_us = _to_chip_epoch_us(utc_now + timedelta(days=365))

        dst_list = [
            {
                "Offset": dst_offset,
                "ValidStarting": 0,
                "ValidUntil": far_future_us,
            }
        ]

        dst_response = await self._async_send_device_command(
            node_id=node_id,
            endpoint_id=endpoint_id,
            cluster_id=TIME_SYNC_CLUSTER_ID,
            command_name="SetDSTOffset",
            payload={"DSTOffset": dst_list},
        )

        if dst_response:
            _LOGGER.debug("SetDSTOffset successful for node %s", node_id)
        else:
            _LOGGER.debug(
                "SetDSTOffset not supported or failed for node %s (continuing anyway)",
                node_id,
            )

        # ---------------------------------------------------------
        # 3) Set UTC Time LAST
        #    PascalCase primary: {"UTCTime": ..., "Granularity": ...}
        #    camelCase fallback: {"utcTime": ..., "granularity": ...}
        # ---------------------------------------------------------
        payload_utc = {
            "UTCTime": utc_microseconds,
            "Granularity": 4,
        }

        _LOGGER.debug(
            "Trying SetUTCTime for node %s, endpoint %s: %s",
            node_id,
            endpoint_id,
            payload_utc,
        )
        time_response = await self._async_send_device_command(
            node_id=node_id,
            endpoint_id=endpoint_id,
            cluster_id=TIME_SYNC_CLUSTER_ID,
            command_name="SetUTCTime",
            payload=payload_utc,
        )

        if not time_response:
            _LOGGER.error("Failed to set UTC time for node %s", node_id)
            return False

        _LOGGER.debug("SetUTCTime successful for node %s", node_id)

        _LOGGER.info(
            "Time synced for node %s: %s (UTC offset: %d, DST: %d)",
            node_id,
            now.isoformat(),
            utc_offset,
            dst_offset,
        )
        return True

    # ------------------------------------------------------------------
    # Bulk sync
    # ------------------------------------------------------------------

    async def async_sync_all_devices(self) -> dict[str, Any]:
        """Sync time on all filtered devices.

        Returns:
            Dict with sync statistics:
            {"success": int, "failed": int, "skipped": int, "errors": list}
        """
        if self._auto_sync_running:
            _LOGGER.warning("Auto-sync already running, skipping this trigger")
            return {"success": 0, "failed": 0, "skipped": 0, "errors": ["Already running"]}

        async with self._auto_sync_lock:
            if self._auto_sync_running:
                _LOGGER.warning("Auto-sync already running (race condition), skipping")
                return {"success": 0, "failed": 0, "skipped": 0, "errors": ["Already running"]}
            self._auto_sync_running = True
            _LOGGER.debug("Auto-sync started, flag set")

        try:
            if not self.is_connected:
                _LOGGER.debug("Connection lost, reconnecting for auto-sync")
                if not await self.async_connect():
                    _LOGGER.error("Failed to connect to Matter Server for auto-sync")
                    return {
                        "success": 0,
                        "failed": 0,
                        "skipped": 0,
                        "errors": ["Failed to connect"],
                    }

            nodes = await self.async_get_matter_nodes()
            if not nodes:
                _LOGGER.warning("No Matter nodes found")
                return {"success": 0, "failed": 0, "skipped": 0, "errors": ["No nodes found"]}

            _LOGGER.debug("Auto-sync: %d devices", len(nodes))

            stats: dict[str, Any] = {"success": 0, "failed": 0, "skipped": 0, "errors": []}

            async def _sync_all() -> None:
                device_filters_raw = self.entry.data.get("device_filter", "")
                device_filter_list = [
                    t.strip().lower()
                    for t in device_filters_raw.split(",")
                    if t.strip()
                ]
                only_time_sync = self.entry.data.get("only_time_sync_devices", True)
                filter_target = self.entry.data.get(
                    CONF_FILTER_TARGET, DEFAULT_FILTER_TARGET
                )

                for node in nodes:
                    node_id = node.get("node_id")
                    node_name = node.get("name", f"Node {node_id}")
                    has_time_sync = node.get("has_time_sync", False)

                    if only_time_sync and not has_time_sync:
                        stats["skipped"] += 1
                        _LOGGER.debug(
                            "Skipping node %s (%s) - no Time Sync cluster",
                            node_id,
                            node_name,
                        )
                        continue

                    candidates = filter_candidates_for_node(node, filter_target)
                    if not device_matches_filter(device_filter_list, candidates):
                        stats["skipped"] += 1
                        _LOGGER.debug(
                            "Skipping node %s (%s) - filtered out",
                            node_id,
                            node_name,
                        )
                        continue

                    _LOGGER.info("Auto-syncing node %s (%s)", node_id, node_name)
                    try:
                        success = await self.async_sync_time(node_id)
                        if success:
                            stats["success"] += 1
                            _LOGGER.debug("✓ Node %s synced successfully", node_id)
                        else:
                            stats["failed"] += 1
                            error_msg = f"Node {node_id} ({node_name}) sync returned False"
                            stats["errors"].append(error_msg)
                            _LOGGER.warning("✗ Node %s sync failed", node_id)
                    except Exception as err:
                        stats["failed"] += 1
                        error_msg = f"Node {node_id} ({node_name}): {err}"
                        stats["errors"].append(error_msg)
                        _LOGGER.error(
                            "✗ Exception syncing node %s (%s): %s",
                            node_id,
                            node_name,
                            err,
                            exc_info=True,
                        )

                _LOGGER.info(
                    "Auto-sync completed: %d successful, %d failed, %d skipped",
                    stats["success"],
                    stats["failed"],
                    stats["skipped"],
                )

                if stats["errors"]:
                    _LOGGER.warning("Auto-sync errors: %s", stats["errors"])

            await asyncio.wait_for(_sync_all(), timeout=120)
            return stats

        except asyncio.TimeoutError:
            _LOGGER.error(
                "Auto-sync exceeded 120s timeout! This may indicate connectivity issues."
            )
            return {"success": 0, "failed": 0, "skipped": 0, "errors": ["Timeout after 120s"]}
        except Exception as err:
            _LOGGER.error("Auto-sync failed with unexpected error: %s", err, exc_info=True)
            return {"success": 0, "failed": 0, "skipped": 0, "errors": [str(err)]}
        finally:
            async with self._auto_sync_lock:
                self._auto_sync_running = False
                _LOGGER.debug("Auto-sync finished, flag cleared")