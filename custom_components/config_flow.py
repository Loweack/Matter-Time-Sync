"""Config flow for Matter Time Sync integration."""
import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

DOMAIN = "matter_time_sync"
_LOGGER = logging.getLogger(__name__)

# Defaults
DEFAULT_WEBSOCKET = "ws://localhost:5580/ws"
DEFAULT_TIMEZONE = "Europe/Paris"

class MatterTimeSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Matter Time Sync."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial setup."""
        if user_input is not None:
            return self.async_create_entry(title="Matter Time Sync", data=user_input)

        data_schema = vol.Schema({
            vol.Required("websocket_address", default=DEFAULT_WEBSOCKET): str,
            vol.Required("timezone", default=DEFAULT_TIMEZONE): str,
        })

        return self.async_show_form(step_id="user", data_schema=data_schema)
