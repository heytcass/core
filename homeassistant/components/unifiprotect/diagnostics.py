"""Diagnostics support for UniFi Network."""

from typing import Any, cast

from uiprotect.test_util.anonymize import anonymize_data

from homeassistant.core import HomeAssistant

from .data import UFPConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: UFPConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    data = config_entry.runtime_data
    api = data.api
    if api.is_public_only:
        # Only the public bootstrap exists for API-key-only entries.
        nvr: dict[str, Any] | None = None
        if api.has_public_bootstrap and api.public_bootstrap.nvr is not None:
            nvr = cast(
                dict[str, Any],
                anonymize_data(api.public_bootstrap.nvr.unifi_dict()),
            )
        return {
            "public_only": True,
            "nvr": nvr,
            "options": dict(config_entry.options),
        }
    bootstrap = cast(dict[str, Any], anonymize_data(api.bootstrap.unifi_dict()))
    return {"bootstrap": bootstrap, "options": dict(config_entry.options)}
