"""UniFi Protect Platform."""

from datetime import timedelta
import logging

from aiohttp.client_exceptions import ServerDisconnectedError
from uiprotect.api import DEVICE_UPDATE_INTERVAL
from uiprotect.data import Bootstrap
from uiprotect.exceptions import BadRequest, ClientError, NotAuthorized

# Import the test_util.anonymize module from the uiprotect package
# in __init__ to ensure it gets imported in the executor since the
# diagnostics module will not be imported in the executor.
from uiprotect.test_util.anonymize import anonymize_data  # noqa: F401

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.typing import ConfigType

from .const import (
    AUTH_RETRIES,
    CONF_ALLOW_EA,
    DEVICES_THAT_ADOPT,
    DOMAIN,
    MIN_REQUIRED_PROTECT_V,
    PLATFORMS,
)
from .data import ProtectData, UFPConfigEntry
from .migrate import async_migrate_data
from .services import async_setup_services
from .utils import (
    _async_unifi_mac_from_hass,
    async_create_api_client,
    async_get_devices,
)
from .views import (
    SnapshotProxyView,
    ThumbnailProxyView,
    VideoEventProxyView,
    VideoProxyView,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=DEVICE_UPDATE_INTERVAL)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the UniFi Protect."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: UFPConfigEntry) -> bool:
    """Set up the UniFi Protect config entries."""

    protect = async_create_api_client(hass, entry)
    _LOGGER.debug("Connect to UniFi Protect")

    # Reuse ProtectData from previous retry or create new
    if hasattr(entry, "runtime_data"):
        data_service = entry.runtime_data
        data_service.api = protect
    else:
        data_service = ProtectData(hass, protect, SCAN_INTERVAL, entry)
        entry.runtime_data = data_service

    if protect.is_public_only:
        return await _async_setup_public_only_entry(hass, entry, data_service)

    try:
        await protect.update()
    except NotAuthorized as err:
        data_service.auth_retries += 1
        if data_service.auth_retries > AUTH_RETRIES:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="entry_auth_failed",
            ) from err
        raise ConfigEntryNotReady from err
    except (TimeoutError, ClientError, ServerDisconnectedError) as err:
        raise ConfigEntryNotReady from err
    bootstrap = protect.bootstrap
    nvr_info = bootstrap.nvr
    auth_user = bootstrap.users.get(bootstrap.auth_user_id)

    # Check if API key is missing
    if not protect.is_api_key_set() and auth_user and nvr_info.can_write(auth_user):
        try:
            new_api_key = await protect.create_api_key(
                name=f"Home Assistant ({hass.config.location_name})"
            )
        except (NotAuthorized, BadRequest) as err:
            _LOGGER.error("Failed to create API key: %s", err)
        else:
            protect.set_api_key(new_api_key)
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_API_KEY: new_api_key}
            )

    if not protect.is_api_key_set():
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="api_key_required",
        )

    if auth_user and auth_user.cloud_account:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "cloud_user",
            is_fixable=True,
            is_persistent=False,
            learn_more_url="https://www.home-assistant.io/integrations/unifiprotect/#local-user",
            severity=IssueSeverity.ERROR,
            translation_key="cloud_user",
            data={"entry_id": entry.entry_id},
        )

    if nvr_info.version < MIN_REQUIRED_PROTECT_V:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="protect_version",
            translation_placeholders={
                "current_version": str(nvr_info.version),
                "min_version": str(MIN_REQUIRED_PROTECT_V),
            },
        )

    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=nvr_info.mac)

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, data_service.async_stop)
    )

    await _async_setup_entry(hass, entry, data_service, bootstrap)

    return True


async def _async_setup_public_only_entry(
    hass: HomeAssistant,
    entry: UFPConfigEntry,
    data_service: ProtectData,
) -> bool:
    """Set up a config entry that authenticates with an API key only.

    Consoles managed through UniFi Fabric with centralized people management
    no longer offer local users, so the private (session based) API is
    unavailable and only the public integration API can be used. Feature
    coverage is limited to what that API exposes.
    """
    protect = data_service.api
    try:
        meta_info = await protect.get_meta_info()
    except NotAuthorized as err:
        data_service.auth_retries += 1
        if data_service.auth_retries > AUTH_RETRIES:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="entry_auth_failed",
            ) from err
        raise ConfigEntryNotReady from err
    except (TimeoutError, ClientError, ServerDisconnectedError) as err:
        raise ConfigEntryNotReady from err

    if meta_info.version < MIN_REQUIRED_PROTECT_V:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="protect_version",
            translation_placeholders={
                "current_version": str(meta_info.version),
                "min_version": str(MIN_REQUIRED_PROTECT_V),
            },
        )

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, data_service.async_stop)
    )

    # Subscribe to the public websockets first, then prime the cache
    # (per library docs), so no updates are missed in between.
    data_service.async_setup()
    try:
        await protect.update_public()
    except NotAuthorized as err:
        await data_service.async_stop()
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="entry_auth_failed",
        ) from err
    except (TimeoutError, ClientError, ServerDisconnectedError) as err:
        await data_service.async_stop()
        raise ConfigEntryNotReady from err

    # update_public() fetches each endpoint best-effort; the NVR endpoint
    # must have succeeded for the entry to be usable.
    if (nvr := protect.public_bootstrap.nvr) is None:
        await data_service.async_stop()
        _LOGGER.debug("NVR data unavailable from public API")
        raise ConfigEntryNotReady

    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=nvr.id)

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, nvr.id)},
        manufacturer="Ubiquiti",
        name=nvr.name or entry.title,
        sw_version=str(meta_info.version),
        configuration_url=protect.base_url,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_views(hass)
    return True


async def _async_setup_entry(
    hass: HomeAssistant,
    entry: UFPConfigEntry,
    data_service: ProtectData,
    bootstrap: Bootstrap,
) -> None:
    await async_migrate_data(hass, entry, data_service.api, bootstrap)
    data_service.async_setup()

    # Prime the public bootstrap. The devices websocket subscription was already
    # registered in async_setup() per library docs (subscribe first, then prime).
    try:
        await data_service.api.update_public()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Public API bootstrap update failed", exc_info=True)

    # Load PTZ patrol data before loading platforms
    await data_service.async_load_ptz_patrols()

    # Create the NVR device before loading platforms
    # This ensures via_device references work for all device entities
    nvr = bootstrap.nvr
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, nvr.mac)},
        identifiers={(DOMAIN, nvr.mac)},
        manufacturer="Ubiquiti",
        name=nvr.display_name,
        model=nvr.type,
        sw_version=str(nvr.version),
        configuration_url=nvr.api.base_url,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_views(hass)


@callback
def _async_register_views(hass: HomeAssistant) -> None:
    hass.http.register_view(ThumbnailProxyView(hass))
    hass.http.register_view(SnapshotProxyView(hass))
    hass.http.register_view(VideoProxyView(hass))
    hass.http.register_view(VideoEventProxyView(hass))


async def async_unload_entry(hass: HomeAssistant, entry: UFPConfigEntry) -> bool:
    """Unload UniFi Protect config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: UFPConfigEntry) -> None:
    """Handle removal of a config entry."""
    # Clear the stored session credentials when the integration is removed
    if entry.state is ConfigEntryState.LOADED:
        # Integration is loaded, use the existing API client
        try:
            await entry.runtime_data.api.clear_session()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to clear session credentials: %s", err)
    else:
        # Integration is not loaded, create temporary client to clear session
        protect = async_create_api_client(hass, entry)
        try:
            await protect.clear_session()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to clear session credentials: %s", err)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: UFPConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove ufp config entry from a device."""
    unifi_macs = {
        _async_unifi_mac_from_hass(connection[1])
        for connection in device_entry.connections
        if connection[0] == dr.CONNECTION_NETWORK_MAC
    }
    data = config_entry.runtime_data
    api = data.api
    if api.is_public_only:
        # The public API exposes no NVR MAC, so the NVR device is matched by
        # its registry identifier instead.
        if (identifier := data.nvr_device_identifier) is not None and (
            identifier in device_entry.identifiers
        ):
            return False
        if api.has_public_bootstrap:
            public_bootstrap = api.public_bootstrap
            public_device_macs = {
                relay.mac for relay in public_bootstrap.relays.values()
            } | {siren.mac for siren in public_bootstrap.sirens.values()}
            if unifi_macs & public_device_macs:
                return False
        return True
    if api.bootstrap.nvr.mac in unifi_macs:
        return False
    for device in async_get_devices(api.bootstrap, DEVICES_THAT_ADOPT):
        if device.is_adopted_by_us and device.mac in unifi_macs:
            return False
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: UFPConfigEntry) -> bool:
    """Migrate entry."""
    _LOGGER.debug("Migrating configuration from version %s", entry.version)

    if entry.version == 1:
        options = dict(entry.options)
        if CONF_ALLOW_EA in options:
            options.pop(CONF_ALLOW_EA)
        hass.config_entries.async_update_entry(
            entry, unique_id=str(entry.unique_id), version=2, options=options
        )

    _LOGGER.debug("Migration to configuration version %s successful", entry.version)

    return True
