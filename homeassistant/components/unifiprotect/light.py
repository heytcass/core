"""Component providing Lights for UniFi Protect."""

import logging
from typing import TYPE_CHECKING, Any, cast

from uiprotect.data import Light, ModelType, ProtectAdoptableDeviceModel, PublicLight
from uiprotect.data.devices import LightDeviceSettings

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import ProtectDeviceType, UFPConfigEntry
from .entity import ProtectDeviceEntity, ProtectPublicDeviceEntity, PublicDeviceWithMac
from .utils import async_ufp_instance_command

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UFPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up lights for UniFi Protect integration."""
    data = entry.runtime_data

    if data.api.is_public_only:
        if data.api.has_public_bootstrap:
            async_add_entities(
                ProtectPublicLight(data, light)
                for light in data.api.public_bootstrap.lights.values()
            )
        return

    @callback
    def _add_new_device(device: ProtectAdoptableDeviceModel) -> None:
        if device.model is ModelType.LIGHT and device.can_write(
            data.api.bootstrap.auth_user
        ):
            async_add_entities([ProtectLight(data, device)])

    data.async_subscribe_adopt(_add_new_device)
    async_add_entities(
        ProtectLight(data, device)
        for device in data.get_by_types({ModelType.LIGHT})
        if device.can_write(data.api.bootstrap.auth_user)
    )


def unifi_brightness_to_hass(value: int) -> int:
    """Convert unifi brightness 1..6 to hass format 0..255."""
    return min(255, round((value / 6) * 255))


def hass_to_unifi_brightness(value: int) -> int:
    """Convert hass brightness 0..255 to unifi 1..6 scale."""
    return max(1, round((value / 255) * 6))


class ProtectLight(ProtectDeviceEntity, LightEntity):
    """A Ubiquiti UniFi Protect Light Entity."""

    device: Light

    _attr_icon = "mdi:spotlight-beam"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _state_attrs = ("_attr_available", "_attr_is_on", "_attr_brightness")

    @callback
    def _async_update_device_from_protect(self, device: ProtectDeviceType) -> None:
        super()._async_update_device_from_protect(device)
        updated_device = self.device
        self._attr_is_on = updated_device.is_light_on
        self._attr_brightness = unifi_brightness_to_hass(
            updated_device.light_device_settings.led_level
        )

    @async_ufp_instance_command
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        led_level: int | None = None
        if brightness is not None:
            led_level = hass_to_unifi_brightness(brightness)
            _LOGGER.debug(
                "Turning on light with brightness %s (led_level=%s)",
                brightness,
                led_level,
            )
        else:
            _LOGGER.debug("Turning on light")

        await self.device.api.update_light_public(
            self.device.id,
            is_light_force_enabled=True,
            light_device_settings=(
                LightDeviceSettings(
                    is_indicator_enabled=self.device.light_device_settings.is_indicator_enabled,
                    led_level=led_level,
                    pir_duration=self.device.light_device_settings.pir_duration,
                    pir_sensitivity=self.device.light_device_settings.pir_sensitivity,
                )
                if led_level is not None
                else None
            ),
        )

    @async_ufp_instance_command
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        _LOGGER.debug("Turning off light")
        await self.device.api.update_light_public(
            self.device.id, is_light_force_enabled=False
        )


class ProtectPublicLight(ProtectPublicDeviceEntity, LightEntity):
    """Light entity backed exclusively by the public API.

    Used for API-key-only (public-only) config entries, where the private
    bootstrap — and with it :class:`ProtectLight` — is unavailable.
    """

    _attr_icon = "mdi:spotlight-beam"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _state_attrs = ("_attr_available", "_attr_is_on", "_attr_brightness")

    @callback
    def _update_from_device(self, device: PublicDeviceWithMac) -> None:
        super()._update_from_device(device)
        if TYPE_CHECKING:
            assert isinstance(device, PublicLight)
        self._attr_is_on = device.is_light_on
        led_level = device.light_device_settings.led_level
        self._attr_brightness = (
            unifi_brightness_to_hass(led_level) if led_level is not None else None
        )

    @property
    def _light(self) -> PublicLight | None:
        api = self.data.api
        if not api.has_public_bootstrap:
            return None
        return api.public_bootstrap.lights.get(self._device_id)

    @async_ufp_instance_command
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        light_device_settings: LightDeviceSettings | None = None
        if brightness is not None and (light := self._light) is not None:
            # update_light_public only consumes the settings' unifi_dict(),
            # so the public settings object satisfies the parameter.
            light_device_settings = cast(
                "LightDeviceSettings",
                light.light_device_settings.model_copy(
                    update={"led_level": hass_to_unifi_brightness(brightness)}
                ),
            )
        await self.data.api.update_light_public(
            self._device_id,
            is_light_force_enabled=True,
            light_device_settings=light_device_settings,
        )

    @async_ufp_instance_command
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.data.api.update_light_public(
            self._device_id, is_light_force_enabled=False
        )
