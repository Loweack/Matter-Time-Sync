"""Config flow for Matter Time Sync integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from zoneinfo import available_timezones

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    DOMAIN,
    CONF_WS_URL,
    CONF_TIMEZONE,
    CONF_DEVICE_FILTER,
    CONF_AUTO_SYNC_ENABLED,
    CONF_AUTO_SYNC_INTERVAL,
    CONF_ONLY_TIME_SYNC_DEVICES,
    DEFAULT_WS_URL,
    DEFAULT_TIMEZONE,
    DEFAULT_DEVICE_FILTER,
    DEFAULT_AUTO_SYNC_ENABLED,
    DEFAULT_AUTO_SYNC_INTERVAL,
    DEFAULT_ONLY_TIME_SYNC_DEVICES,
    AUTO_SYNC_INTERVALS,
)

_LOGGER = logging.getLogger(__name__)

# New (local) option key: which field(s) the device_filter should match against.
CONF_FILTER_TARGET = "filter_target"
DEFAULT_FILTER_TARGET = "any"  # any | display_name | ha_name | matter

_TIMEZONE_CACHE: list[str] | None = None


def _get_sorted_timezones_sync() -> list[str]:
    all_zones = sorted(available_timezones())
    filtered_zones = [
        tz
        for tz in all_zones
        if not tz.startswith(("posix/", "right/", "Etc/")) and "/" in tz
    ]
    return ["UTC"] + filtered_zones


async def async_get_sorted_timezones(hass: HomeAssistant) -> list[str]:
    global _TIMEZONE_CACHE
    if _TIMEZONE_CACHE is not None:
        return _TIMEZONE_CACHE
    loop = asyncio.get_event_loop()
    _TIMEZONE_CACHE = await loop.run_in_executor(None, _get_sorted_timezones_sync)
    return _TIMEZONE_CACHE


def get_matter_server_url(hass: HomeAssistant) -> str | None:
    for entry in hass.config_entries.async_entries("matter"):
        if entry.state == config_entries.ConfigEntryState.LOADED:
            url = entry.data.get("url")
            if url:
                _LOGGER.debug("Found Matter server URL: %s", url)
                return url
    return None


def get_ha_timezone(hass: HomeAssistant) -> str:
    return hass.config.time_zone or DEFAULT_TIMEZONE


async def validate_ws_connection(ws_url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=5) as ws:
                await ws.send_json({"message_id": "test", "command": "get_nodes"})
                msg = await ws.receive(timeout=5)
                return msg.type == aiohttp.WSMsgType.TEXT
    except Exception as err:
        _LOGGER.warning("Could not connect to Matter server at %s: %s", ws_url, err)
        return False


def get_auto_sync_interval_options() -> list[dict]:
    labels = {15: "15 min", 30: "30 min", 60: "1 h", 120: "2 h", 360: "6 h", 720: "12 h", 1440: "24 h"}
    return [{"value": str(i), "label": labels.get(i, f"{i} min")} for i in AUTO_SYNC_INTERVALS]


def get_filter_target_options() -> list[dict]:
    return [
        {"value": "any", "label": "Any (name/label/product)"},
        {"value": "display_name", "label": "Display name"},
        {"value": "ha_name", "label": "HA name only"},
        {"value": "matter", "label": "Matter product/label"},
    ]


class MatterTimeSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Matter Time Sync."""

    VERSION = 3

    def __init__(self) -> None:
        self._discovered_url: str | None = None
        self._discovered_timezone: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if self._discovered_url is None:
            self._discovered_url = get_matter_server_url(self.hass)
        if self._discovered_timezone is None:
            self._discovered_timezone = get_ha_timezone(self.hass)

        suggested_url = self._discovered_url or DEFAULT_WS_URL

        if user_input is not None:
            ws_url = user_input[CONF_WS_URL]
            timezone = user_input[CONF_TIMEZONE]

            if not await validate_ws_connection(ws_url):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="Matter Time Sync",
                    data={
                        CONF_WS_URL: ws_url,
                        CONF_TIMEZONE: timezone,
                        CONF_DEVICE_FILTER: user_input.get(CONF_DEVICE_FILTER, DEFAULT_DEVICE_FILTER),
                        CONF_FILTER_TARGET: user_input.get(CONF_FILTER_TARGET, DEFAULT_FILTER_TARGET),
                        CONF_AUTO_SYNC_ENABLED: user_input.get(CONF_AUTO_SYNC_ENABLED, DEFAULT_AUTO_SYNC_ENABLED),
                        CONF_AUTO_SYNC_INTERVAL: int(user_input.get(CONF_AUTO_SYNC_INTERVAL, DEFAULT_AUTO_SYNC_INTERVAL)),
                        CONF_ONLY_TIME_SYNC_DEVICES: user_input.get(CONF_ONLY_TIME_SYNC_DEVICES, DEFAULT_ONLY_TIME_SYNC_DEVICES),
                    },
                )

        timezone_options = await async_get_sorted_timezones(self.hass)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_WS_URL, default=suggested_url): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Required(CONF_TIMEZONE, default=self._discovered_timezone): SelectSelector(
                    SelectSelectorConfig(options=timezone_options, mode=SelectSelectorMode.DROPDOWN, sort=False)
                ),
                vol.Optional(CONF_DEVICE_FILTER, default=DEFAULT_DEVICE_FILTER): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=False)
                ),
                vol.Optional(CONF_FILTER_TARGET, default=DEFAULT_FILTER_TARGET): SelectSelector(
                    SelectSelectorConfig(options=get_filter_target_options(), mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_AUTO_SYNC_ENABLED, default=DEFAULT_AUTO_SYNC_ENABLED): BooleanSelector(),
                vol.Optional(CONF_AUTO_SYNC_INTERVAL, default=str(DEFAULT_AUTO_SYNC_INTERVAL)): SelectSelector(
                    SelectSelectorConfig(options=get_auto_sync_interval_options(), mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_ONLY_TIME_SYNC_DEVICES, default=DEFAULT_ONLY_TIME_SYNC_DEVICES): BooleanSelector(),
            }
        )

        description_placeholders: dict[str, str] = {}
        description_placeholders["detection_status"] = (
            "✅ Matter Server auto-detected!" if self._discovered_url else "⚠️ No Matter integration found. Please verify URL manually."
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors, description_placeholders=description_placeholders)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return MatterTimeSyncOptionsFlow()


class MatterTimeSyncOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Matter Time Sync."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            ws_url = user_input[CONF_WS_URL]

            if not await validate_ws_connection(ws_url):
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        CONF_WS_URL: ws_url,
                        CONF_TIMEZONE: user_input[CONF_TIMEZONE],
                        CONF_DEVICE_FILTER: user_input.get(CONF_DEVICE_FILTER, DEFAULT_DEVICE_FILTER),
                        CONF_FILTER_TARGET: user_input.get(CONF_FILTER_TARGET, DEFAULT_FILTER_TARGET),
                        CONF_AUTO_SYNC_ENABLED: user_input.get(CONF_AUTO_SYNC_ENABLED, DEFAULT_AUTO_SYNC_ENABLED),
                        CONF_AUTO_SYNC_INTERVAL: int(user_input.get(CONF_AUTO_SYNC_INTERVAL, DEFAULT_AUTO_SYNC_INTERVAL)),
                        CONF_ONLY_TIME_SYNC_DEVICES: user_input.get(CONF_ONLY_TIME_SYNC_DEVICES, DEFAULT_ONLY_TIME_SYNC_DEVICES),
                    },
                )
                return self.async_create_entry(title="", data={})

        current_url = self.config_entry.data.get(CONF_WS_URL, DEFAULT_WS_URL)
        current_timezone = self.config_entry.data.get(CONF_TIMEZONE, get_ha_timezone(self.hass))
        current_filter = self.config_entry.data.get(CONF_DEVICE_FILTER, DEFAULT_DEVICE_FILTER)
        current_filter_target = self.config_entry.data.get(CONF_FILTER_TARGET, DEFAULT_FILTER_TARGET)
        current_auto_sync = self.config_entry.data.get(CONF_AUTO_SYNC_ENABLED, DEFAULT_AUTO_SYNC_ENABLED)
        current_interval = self.config_entry.data.get(CONF_AUTO_SYNC_INTERVAL, DEFAULT_AUTO_SYNC_INTERVAL)
        current_only_time_sync = self.config_entry.data.get(CONF_ONLY_TIME_SYNC_DEVICES, DEFAULT_ONLY_TIME_SYNC_DEVICES)

        timezone_options = await async_get_sorted_timezones(self.hass)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_WS_URL, default=current_url): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Required(CONF_TIMEZONE, default=current_timezone): SelectSelector(
                    SelectSelectorConfig(options=timezone_options, mode=SelectSelectorMode.DROPDOWN, sort=False)
                ),
                vol.Optional(CONF_DEVICE_FILTER, default=current_filter): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=False)
                ),
                vol.Optional(CONF_FILTER_TARGET, default=current_filter_target): SelectSelector(
                    SelectSelectorConfig(options=get_filter_target_options(), mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_AUTO_SYNC_ENABLED, default=current_auto_sync): BooleanSelector(),
                vol.Optional(CONF_AUTO_SYNC_INTERVAL, default=str(current_interval)): SelectSelector(
                    SelectSelectorConfig(options=get_auto_sync_interval_options(), mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_ONLY_TIME_SYNC_DEVICES, default=current_only_time_sync): BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema, errors=errors)
