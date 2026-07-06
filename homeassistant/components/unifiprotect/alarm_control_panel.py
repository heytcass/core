"""Support for UniFi Protect NVR alarm control panel."""

from uiprotect.data import NVR, NvrArmModeStatus
from uiprotect.exceptions import GlobalAlarmManagerError

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_ATTRIBUTION, DOMAIN
from .data import ProtectData, ProtectDeviceType, UFPConfigEntry
from .entity import ProtectNVREntity
from .utils import async_ufp_instance_command

PARALLEL_UPDATES = 0

_UIPROTECT_TO_HA: dict[NvrArmModeStatus, AlarmControlPanelState] = {
    NvrArmModeStatus.DISABLED: AlarmControlPanelState.DISARMED,
    NvrArmModeStatus.ARMING: AlarmControlPanelState.ARMING,
    NvrArmModeStatus.ARMED: AlarmControlPanelState.ARMED_AWAY,
    NvrArmModeStatus.BREACH: AlarmControlPanelState.TRIGGERED,
    NvrArmModeStatus.UNKNOWN: AlarmControlPanelState.DISARMED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UFPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up alarm control panel for UniFi Protect NVR."""
    data = entry.runtime_data
    api = data.api

    # No public Integration API available (e.g. older NVR firmware that does
    # not expose the Alarm Manager endpoint, or no API key configured).
    # Skip entity creation entirely; we cannot represent the alarm state.
    if not api.has_public_bootstrap:
        return

    # ``arm_mode`` is ``None`` on NVR firmware that predates the Alarm Manager
    # public API. Skip entity creation so the user does not see a permanently
    # unavailable entity.
    if api.public_bootstrap.arm_mode is None:
        return

    if api.is_public_only:
        if (nvr_id := data.nvr_id) is not None:
            async_add_entities([ProtectPublicNVRAlarmControlPanel(data, nvr_id)])
        return

    nvr = api.bootstrap.nvr
    async_add_entities([ProtectNVRAlarmControlPanel(data, device=nvr)])


class ProtectAlarmCommandsMixin(AlarmControlPanelEntity):
    """Arm/disarm commands shared by the private and public-only entities."""

    data: ProtectData

    @async_ufp_instance_command
    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Send disarm command."""
        try:
            await self.data.api.disable_arm_alarm_public()
        except GlobalAlarmManagerError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="global_alarm_manager",
            ) from err

    @async_ufp_instance_command
    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Send arm away command (arms with the currently selected profile)."""
        try:
            await self.data.api.enable_arm_alarm_public()
        except GlobalAlarmManagerError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="global_alarm_manager",
            ) from err


class ProtectNVRAlarmControlPanel(ProtectNVREntity, ProtectAlarmCommandsMixin):
    """UniFi Protect NVR Alarm Control Panel."""

    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_translation_key = "nvr_alarm"
    _state_attrs = ("_attr_available", "_attr_alarm_state")

    def __init__(self, data: ProtectData, device: NVR) -> None:
        """Initialize the alarm control panel."""
        super().__init__(data, device, EntityDescription(key="alarm"))
        self._refresh_alarm_state()

    @callback
    def _refresh_alarm_state(self) -> None:
        """Update _attr_alarm_state from the public bootstrap cache."""
        api = self.data.api
        arm_mode = api.public_bootstrap.arm_mode if api.has_public_bootstrap else None
        if arm_mode is None:
            # No alarm data available — force unavailable regardless of the
            # private WebSocket state managed by the base class.
            self._attr_available = False
            self._attr_alarm_state = None
            return
        # Do NOT set _attr_available = True here.  Availability when alarm data
        # is present is determined exclusively by the base class via
        # last_update_success (private WebSocket health). Only force it to
        # False as an additional condition when alarm data is missing.
        # Fall back to DISARMED for unknown future status values rather than
        # rendering the entity as ``unknown``.
        self._attr_alarm_state = _UIPROTECT_TO_HA.get(
            arm_mode.status, AlarmControlPanelState.DISARMED
        )

    @callback
    def _async_update_device_from_protect(self, device: ProtectDeviceType) -> None:
        super()._async_update_device_from_protect(device)
        self._refresh_alarm_state()


class ProtectPublicNVRAlarmControlPanel(ProtectAlarmCommandsMixin):
    """NVR alarm control panel backed exclusively by the public API.

    Used for API-key-only (public-only) config entries, where the private
    bootstrap — and with it :class:`ProtectNVREntity` — is unavailable.
    """

    _attr_attribution = DEFAULT_ATTRIBUTION
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_translation_key = "nvr_alarm"

    def __init__(self, data: ProtectData, nvr_id: str) -> None:
        """Initialize the alarm control panel."""
        self.data = data
        self._attr_unique_id = f"{nvr_id}_alarm"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, nvr_id)})
        self._refresh_alarm_state()

    @callback
    def _refresh_alarm_state(self) -> None:
        """Update state and availability from the public bootstrap cache."""
        api = self.data.api
        arm_mode = api.public_bootstrap.arm_mode if api.has_public_bootstrap else None
        if arm_mode is None:
            self._attr_available = False
            self._attr_alarm_state = None
            return
        self._attr_available = self.data.last_update_success
        self._attr_alarm_state = _UIPROTECT_TO_HA.get(
            arm_mode.status, AlarmControlPanelState.DISARMED
        )

    @callback
    def _async_updated(self) -> None:
        """Handle a public NVR update dispatched by ProtectData."""
        previous_state = (self._attr_available, self._attr_alarm_state)
        self._refresh_alarm_state()
        if (self._attr_available, self._attr_alarm_state) != previous_state:
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to public NVR updates dispatched by ProtectData."""
        await super().async_added_to_hass()
        self.async_on_remove(self.data.async_subscribe_public_nvr(self._async_updated))
