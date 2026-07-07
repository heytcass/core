"""Test the UniFi Protect light platform."""

from unittest.mock import AsyncMock, Mock

from uiprotect.data import DeviceState, Light, ModelType, PublicLight
from uiprotect.data.types import LEDLevel

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.components.unifiprotect.const import DEFAULT_ATTRIBUTION
from homeassistant.const import (
    ATTR_ATTRIBUTION,
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .utils import (
    MockUFPFixture,
    adopt_devices,
    assert_entity_counts,
    init_entry,
    remove_entities,
)


async def test_light_remove(
    hass: HomeAssistant, ufp: MockUFPFixture, light: Light
) -> None:
    """Test removing and re-adding a light device."""

    await init_entry(hass, ufp, [light])
    assert_entity_counts(hass, Platform.LIGHT, 1, 1)
    await remove_entities(hass, ufp, [light])
    assert_entity_counts(hass, Platform.LIGHT, 0, 0)
    await adopt_devices(hass, ufp, [light])
    assert_entity_counts(hass, Platform.LIGHT, 1, 1)


async def test_light_setup(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    ufp: MockUFPFixture,
    light: Light,
    unadopted_light: Light,
) -> None:
    """Test light entity setup."""

    await init_entry(hass, ufp, [light, unadopted_light])
    assert_entity_counts(hass, Platform.LIGHT, 1, 1)

    unique_id = light.mac
    entity_id = "light.test_light"

    entity = entity_registry.async_get(entity_id)
    assert entity
    assert entity.unique_id == unique_id

    state = hass.states.get(entity_id)
    assert state
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_ATTRIBUTION] == DEFAULT_ATTRIBUTION


async def test_light_update(
    hass: HomeAssistant, ufp: MockUFPFixture, light: Light, unadopted_light: Light
) -> None:
    """Test light entity update."""

    await init_entry(hass, ufp, [light, unadopted_light])
    assert_entity_counts(hass, Platform.LIGHT, 1, 1)

    new_light = light.model_copy()
    new_light.is_light_on = True
    new_light.light_device_settings.led_level = LEDLevel(3)

    mock_msg = Mock()
    mock_msg.changed_data = {}
    mock_msg.new_obj = new_light

    ufp.api.bootstrap.lights = {new_light.id: new_light}
    ufp.ws_msg(mock_msg)
    await hass.async_block_till_done()

    state = hass.states.get("light.test_light")
    assert state
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 128


async def test_light_turn_on(
    hass: HomeAssistant, ufp: MockUFPFixture, light: Light, unadopted_light: Light
) -> None:
    """Test light entity turn on."""

    light._api = ufp.api
    light.api.update_light_public = AsyncMock()

    await init_entry(hass, ufp, [light, unadopted_light])
    assert_entity_counts(hass, Platform.LIGHT, 1, 1)

    entity_id = "light.test_light"
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert light.api.update_light_public.called
    light.api.update_light_public.assert_called_once_with(
        light.id, is_light_force_enabled=True, light_device_settings=None
    )


async def test_light_turn_on_with_brightness(
    hass: HomeAssistant, ufp: MockUFPFixture, light: Light, unadopted_light: Light
) -> None:
    """Test light entity turn on with brightness."""

    light._api = ufp.api
    light.api.update_light_public = AsyncMock()

    await init_entry(hass, ufp, [light, unadopted_light])
    assert_entity_counts(hass, Platform.LIGHT, 1, 1)

    entity_id = "light.test_light"
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: entity_id, ATTR_BRIGHTNESS: 128},
        blocking=True,
    )

    assert light.api.update_light_public.called
    call_kwargs = light.api.update_light_public.call_args[1]
    assert call_kwargs["is_light_force_enabled"] is True
    assert call_kwargs["light_device_settings"] is not None
    assert call_kwargs["light_device_settings"].led_level == 3  # 128/255 * 6 ≈ 3


async def test_light_turn_off(
    hass: HomeAssistant, ufp: MockUFPFixture, light: Light, unadopted_light: Light
) -> None:
    """Test light entity turn off."""

    light._api = ufp.api
    light.api.update_light_public = AsyncMock()

    await init_entry(hass, ufp, [light, unadopted_light])
    assert_entity_counts(hass, Platform.LIGHT, 1, 1)

    entity_id = "light.test_light"
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert light.api.update_light_public.called
    light.api.update_light_public.assert_called_once_with(
        light.id, is_light_force_enabled=False
    )


def _make_public_light() -> Mock:
    """Create a public API light for testing."""
    light = Mock(spec=PublicLight)
    light.id = "test_public_light_id"
    light.mac = "LIGHT0000001"
    light.name = "Driveway Light"
    light.model = ModelType.LIGHT
    light.state = DeviceState.CONNECTED
    light.is_light_on = False
    light.light_device_settings = Mock(led_level=6)
    return light


async def test_public_only_light(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    ufp_public: MockUFPFixture,
) -> None:
    """Test light entity on an API-key-only (public-only) entry."""
    light = _make_public_light()
    ufp_public.api.public_bootstrap.lights = {light.id: light}
    ufp_public.api.update_light_public = AsyncMock()

    await hass.config_entries.async_setup(ufp_public.entry.entry_id)
    await hass.async_block_till_done()

    entity_id = "light.driveway_light"
    entity = entity_registry.async_get(entity_id)
    assert entity is not None
    assert entity.unique_id == "LIGHT0000001"

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    ufp_public.api.update_light_public.assert_called_with(
        "test_public_light_id",
        is_light_force_enabled=True,
        light_device_settings=None,
    )

    await hass.services.async_call(
        "light",
        "turn_off",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    ufp_public.api.update_light_public.assert_called_with(
        "test_public_light_id", is_light_force_enabled=False
    )


async def test_public_only_light_ws_update(
    hass: HomeAssistant,
    ufp_public: MockUFPFixture,
) -> None:
    """Test public devices WS updates refresh the public light."""
    light = _make_public_light()
    ufp_public.api.public_bootstrap.lights = {light.id: light}

    await hass.config_entries.async_setup(ufp_public.entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("light.driveway_light")
    assert state is not None
    assert state.state == STATE_OFF

    turned_on = _make_public_light()
    turned_on.is_light_on = True
    ufp_public.api.public_bootstrap.lights = {turned_on.id: turned_on}

    mock_msg = Mock()
    mock_msg.changed_data = {}
    mock_msg.new_obj = turned_on
    assert ufp_public.devices_ws_subscription is not None
    ufp_public.devices_ws_subscription(mock_msg)
    await hass.async_block_till_done()

    state = hass.states.get("light.driveway_light")
    assert state is not None
    assert state.state == STATE_ON
