"""Component providing binary sensors for UniFi Protect."""

from collections.abc import Callable, Sequence
import dataclasses
from typing import cast

from uiprotect.data import (
    NVR,
    Camera,
    DeviceState,
    Event,
    EventType,
    ModelType,
    MountType,
    ProtectAdoptableDeviceModel,
    PublicCamera,
    PublicLight,
    PublicSensor,
    Sensor,
    SmartDetectObjectType,
)
from uiprotect.data.nvr import UOSDisk

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DEFAULT_ATTRIBUTION, DEFAULT_BRAND, DOMAIN
from .data import ProtectData, ProtectDeviceType, UFPConfigEntry
from .entity import (
    BaseProtectEntity,
    EventEntityMixin,
    PermRequired,
    ProtectDeviceEntity,
    ProtectEntityDescription,
    ProtectEventMixin,
    ProtectIsOnEntity,
    ProtectNVREntity,
    ProtectPublicDeviceEntity,
    PublicDeviceWithMac,
    async_all_device_entities,
)

_KEY_DOOR = "door"
PARALLEL_UPDATES = 0


@dataclasses.dataclass(frozen=True, kw_only=True)
class ProtectBinaryEntityDescription(
    ProtectEntityDescription, BinarySensorEntityDescription
):
    """Describes UniFi Protect Binary Sensor entity."""


@dataclasses.dataclass(frozen=True, kw_only=True)
class ProtectBinaryEventEntityDescription(
    ProtectEventMixin, BinarySensorEntityDescription
):
    """Describes UniFi Protect Binary Sensor entity."""


MOUNT_DEVICE_CLASS_MAP = {
    MountType.GARAGE: BinarySensorDeviceClass.GARAGE_DOOR,
    MountType.WINDOW: BinarySensorDeviceClass.WINDOW,
    MountType.DOOR: BinarySensorDeviceClass.DOOR,
}


CAMERA_SENSORS: tuple[ProtectBinaryEntityDescription, ...] = (
    ProtectBinaryEntityDescription(
        key="dark",
        translation_key="is_dark",
        ufp_value="is_dark",
    ),
    ProtectBinaryEntityDescription(
        key="ssh",
        translation_key="ssh_enabled",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="is_ssh_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="status_light",
        translation_key="status_light",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="feature_flags.has_led_status",
        ufp_value="led_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="hdr_mode",
        translation_key="hdr_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="feature_flags.has_hdr",
        ufp_value="hdr_mode",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="high_fps",
        translation_key="high_fps",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="feature_flags.has_highfps",
        ufp_value="is_high_fps_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="system_sounds",
        translation_key="system_sounds",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="has_speaker",
        ufp_value="speaker_settings.are_system_sounds_enabled",
        ufp_enabled="feature_flags.has_speaker",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="osd_name",
        translation_key="overlay_show_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="osd_settings.is_name_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="osd_date",
        translation_key="overlay_show_date",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="osd_settings.is_date_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="osd_logo",
        translation_key="overlay_show_logo",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="osd_settings.is_logo_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="osd_bitrate",
        translation_key="overlay_show_nerd_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="osd_settings.is_debug_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="motion_enabled",
        translation_key="detections_motion",
        ufp_value="recording_settings.enable_motion_detection",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_person",
        translation_key="detections_person",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_person",
        ufp_value="is_person_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_vehicle",
        translation_key="detections_vehicle",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_vehicle",
        ufp_value="is_vehicle_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_animal",
        translation_key="detections_animal",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_animal",
        ufp_value="is_animal_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_package",
        translation_key="detections_package",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_package",
        ufp_value="is_package_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_licenseplate",
        translation_key="detections_license_plate",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_license_plate",
        ufp_value="is_license_plate_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_smoke",
        translation_key="detections_smoke",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_smoke",
        ufp_value="is_smoke_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_cmonx",
        translation_key="detections_co_alarm",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_co",
        ufp_value="is_co_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_siren",
        translation_key="detections_siren",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_siren",
        ufp_value="is_siren_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_baby_cry",
        translation_key="detections_baby_cry",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_baby_cry",
        ufp_value="is_baby_cry_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_speak",
        translation_key="detections_speaking",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_speaking",
        ufp_value="is_speaking_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_bark",
        translation_key="detections_barking",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_bark",
        ufp_value="is_bark_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_car_alarm",
        translation_key="detections_car_alarm",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_car_alarm",
        ufp_value="is_car_alarm_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_car_horn",
        translation_key="detections_car_horn",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_car_horn",
        ufp_value="is_car_horn_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="smart_glass_break",
        translation_key="detections_glass_break",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="can_detect_glass_break",
        ufp_value="is_glass_break_detection_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="track_person",
        translation_key="tracking_person",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_required_field="feature_flags.is_ptz",
        ufp_value="is_person_tracking_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
)

LIGHT_SENSORS: tuple[ProtectBinaryEntityDescription, ...] = (
    ProtectBinaryEntityDescription(
        key="dark",
        translation_key="is_dark",
        ufp_value="is_dark",
    ),
    ProtectBinaryEntityDescription(
        key="motion",
        device_class=BinarySensorDeviceClass.MOTION,
        ufp_value="is_pir_motion_detected",
    ),
    ProtectBinaryEntityDescription(
        key="light",
        translation_key="flood_light",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="is_light_on",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="ssh",
        translation_key="ssh_enabled",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="is_ssh_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="status_light",
        translation_key="status_light",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="light_device_settings.is_indicator_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
)

# The mountable sensors can be remounted at run-time which
# means they can change their device class at run-time.
MOUNTABLE_SENSE_SENSORS: tuple[ProtectBinaryEntityDescription, ...] = (
    ProtectBinaryEntityDescription(
        key=_KEY_DOOR,
        translation_key="contact",
        device_class=BinarySensorDeviceClass.DOOR,
        ufp_value="is_opened",
        ufp_enabled="is_contact_sensor_enabled",
    ),
)

SENSE_SENSORS: tuple[ProtectBinaryEntityDescription, ...] = (
    ProtectBinaryEntityDescription(
        key="leak",
        device_class=BinarySensorDeviceClass.MOISTURE,
        ufp_value="is_leak_detected",
        ufp_enabled="is_leak_sensor_enabled",
    ),
    ProtectBinaryEntityDescription(
        key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="battery_status.is_low",
    ),
    ProtectBinaryEntityDescription(
        key="motion",
        device_class=BinarySensorDeviceClass.MOTION,
        ufp_value="is_motion_detected",
        ufp_enabled="is_motion_sensor_enabled",
    ),
    ProtectBinaryEntityDescription(
        key="tampering",
        device_class=BinarySensorDeviceClass.TAMPER,
        ufp_value="is_tampering_detected",
    ),
    ProtectBinaryEntityDescription(
        key="status_light",
        translation_key="status_light",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="led_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="motion_enabled",
        translation_key="motion_detection_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="motion_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="temperature",
        translation_key="temperature_sensor_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="temperature_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="humidity",
        translation_key="humidity_sensor_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="humidity_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="light",
        translation_key="light_sensor_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="light_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
    ProtectBinaryEntityDescription(
        key="alarm",
        translation_key="alarm_sound_detection",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="alarm_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
)

EVENT_SENSORS: tuple[ProtectBinaryEventEntityDescription, ...] = (
    ProtectBinaryEventEntityDescription(
        key="doorbell",
        translation_key="doorbell",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        ufp_required_field="feature_flags.is_doorbell",
        ufp_event_obj="last_ring_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="motion",
        device_class=BinarySensorDeviceClass.MOTION,
        ufp_enabled="is_motion_detection_on",
        ufp_event_obj="last_motion_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_obj_any",
        translation_key="object_detected",
        ufp_required_field="feature_flags.has_smart_detect",
        ufp_event_obj="last_smart_detect_event",
        entity_registry_enabled_default=False,
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_obj_person",
        translation_key="person_detected",
        ufp_obj_type=SmartDetectObjectType.PERSON,
        ufp_required_field="can_detect_person",
        ufp_enabled="is_person_detection_on",
        ufp_event_obj="last_person_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_obj_vehicle",
        translation_key="vehicle_detected",
        ufp_obj_type=SmartDetectObjectType.VEHICLE,
        ufp_required_field="can_detect_vehicle",
        ufp_enabled="is_vehicle_detection_on",
        ufp_event_obj="last_vehicle_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_obj_animal",
        translation_key="animal_detected",
        ufp_obj_type=SmartDetectObjectType.ANIMAL,
        ufp_required_field="can_detect_animal",
        ufp_enabled="is_animal_detection_on",
        ufp_event_obj="last_animal_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_obj_package",
        translation_key="package_detected",
        entity_registry_enabled_default=False,
        ufp_obj_type=SmartDetectObjectType.PACKAGE,
        ufp_required_field="can_detect_package",
        ufp_enabled="is_package_detection_on",
        ufp_event_obj="last_package_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_any",
        translation_key="audio_object_detected",
        ufp_required_field="feature_flags.has_smart_detect",
        ufp_event_obj="last_smart_audio_detect_event",
        entity_registry_enabled_default=False,
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_smoke",
        translation_key="smoke_alarm_detected",
        ufp_obj_type=SmartDetectObjectType.SMOKE,
        ufp_required_field="can_detect_smoke",
        ufp_enabled="is_smoke_detection_on",
        ufp_event_obj="last_smoke_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_cmonx",
        translation_key="co_alarm_detected",
        device_class=BinarySensorDeviceClass.CO,
        ufp_required_field="can_detect_co",
        ufp_enabled="is_co_detection_on",
        ufp_event_obj="last_cmonx_detect_event",
        ufp_obj_type=SmartDetectObjectType.CMONX,
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_siren",
        translation_key="siren_detected",
        ufp_obj_type=SmartDetectObjectType.SIREN,
        ufp_required_field="can_detect_siren",
        ufp_enabled="is_siren_detection_on",
        ufp_event_obj="last_siren_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_baby_cry",
        translation_key="baby_cry_detected",
        ufp_obj_type=SmartDetectObjectType.BABY_CRY,
        ufp_required_field="can_detect_baby_cry",
        ufp_enabled="is_baby_cry_detection_on",
        ufp_event_obj="last_baby_cry_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_speak",
        translation_key="speaking_detected",
        ufp_obj_type=SmartDetectObjectType.SPEAK,
        ufp_required_field="can_detect_speaking",
        ufp_enabled="is_speaking_detection_on",
        ufp_event_obj="last_speaking_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_bark",
        translation_key="barking_detected",
        ufp_obj_type=SmartDetectObjectType.BARK,
        ufp_required_field="can_detect_bark",
        ufp_enabled="is_bark_detection_on",
        ufp_event_obj="last_bark_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_car_alarm",
        translation_key="car_alarm_detected",
        ufp_obj_type=SmartDetectObjectType.BURGLAR,
        ufp_required_field="can_detect_car_alarm",
        ufp_enabled="is_car_alarm_detection_on",
        ufp_event_obj="last_car_alarm_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_car_horn",
        translation_key="car_horn_detected",
        ufp_obj_type=SmartDetectObjectType.CAR_HORN,
        ufp_required_field="can_detect_car_horn",
        ufp_enabled="is_car_horn_detection_on",
        ufp_event_obj="last_car_horn_detect_event",
    ),
    ProtectBinaryEventEntityDescription(
        key="smart_audio_glass_break",
        translation_key="glass_break_detected",
        ufp_obj_type=SmartDetectObjectType.GLASS_BREAK,
        ufp_required_field="can_detect_glass_break",
        ufp_enabled="is_glass_break_detection_on",
        ufp_event_obj="last_glass_break_detect_event",
    ),
)

DOORLOCK_SENSORS: tuple[ProtectBinaryEntityDescription, ...] = (
    ProtectBinaryEntityDescription(
        key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="battery_status.is_low",
    ),
    ProtectBinaryEntityDescription(
        key="status_light",
        translation_key="status_light",
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="led_settings.is_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
)

VIEWER_SENSORS: tuple[ProtectBinaryEntityDescription, ...] = (
    ProtectBinaryEntityDescription(
        key="ssh",
        translation_key="ssh_enabled",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value="is_ssh_enabled",
        ufp_perm=PermRequired.NO_WRITE,
    ),
)


DISK_SENSORS: tuple[ProtectBinaryEntityDescription, ...] = (
    ProtectBinaryEntityDescription(
        key="disk_health",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

_MODEL_DESCRIPTIONS: dict[ModelType, Sequence[ProtectEntityDescription]] = {
    ModelType.CAMERA: CAMERA_SENSORS,
    ModelType.LIGHT: LIGHT_SENSORS,
    ModelType.SENSOR: SENSE_SENSORS,
    ModelType.DOORLOCK: DOORLOCK_SENSORS,
    ModelType.VIEWPORT: VIEWER_SENSORS,
}

_MOUNTABLE_MODEL_DESCRIPTIONS: dict[ModelType, Sequence[ProtectEntityDescription]] = {
    ModelType.SENSOR: MOUNTABLE_SENSE_SENSORS,
}


class ProtectDeviceBinarySensor(
    ProtectIsOnEntity, ProtectDeviceEntity, BinarySensorEntity
):
    """A UniFi Protect Device Binary Sensor."""

    entity_description: ProtectBinaryEntityDescription


class MountableProtectDeviceBinarySensor(ProtectDeviceBinarySensor):
    """A UniFi Protect Device Binary Sensor that can change device class at runtime."""

    device: Sensor
    _state_attrs = ("_attr_available", "_attr_is_on", "_attr_device_class")

    @callback
    def _async_update_device_from_protect(self, device: ProtectDeviceType) -> None:
        super()._async_update_device_from_protect(device)
        # UP Sense can be any of the 3 contact sensor device classes
        self._attr_device_class = MOUNT_DEVICE_CLASS_MAP.get(
            self.device.mount_type, BinarySensorDeviceClass.DOOR
        )


class ProtectDiskBinarySensor(ProtectNVREntity, BinarySensorEntity):
    """A UniFi Protect NVR Disk Binary Sensor."""

    _disk: UOSDisk
    entity_description: ProtectBinaryEntityDescription
    _state_attrs = ("_attr_available", "_attr_is_on")

    def __init__(
        self,
        data: ProtectData,
        device: NVR,
        description: ProtectBinaryEntityDescription,
        disk: UOSDisk,
    ) -> None:
        """Initialize the Binary Sensor."""
        self._disk = disk
        # backwards compat with old unique IDs
        index = self._disk.slot - 1
        description = dataclasses.replace(
            description,
            key=f"{description.key}_{index}",
            name=f"{disk.type} {disk.slot}",
        )
        super().__init__(data, device, description)

    @callback
    def _async_update_device_from_protect(self, device: ProtectDeviceType) -> None:
        super()._async_update_device_from_protect(device)
        slot = self._disk.slot
        self._attr_available = False
        available = self.data.last_update_success

        # should not be possible since it would require user to
        # _downgrade_ to make ustorage disappear
        assert self.device.system_info.ustorage is not None
        for disk in self.device.system_info.ustorage.disks:
            if disk.slot == slot:
                self._disk = disk
                self._attr_available = available
                break

        self._attr_is_on = not self._disk.is_healthy


class ProtectEventBinarySensor(EventEntityMixin, BinarySensorEntity):
    """A UniFi Protect Device Binary Sensor for events."""

    entity_description: ProtectBinaryEventEntityDescription
    _state_attrs = ("_attr_available", "_attr_is_on", "_attr_extra_state_attributes")

    @callback
    def _set_event_done(self) -> None:
        self._attr_is_on = False
        self._attr_extra_state_attributes = {}

    @callback
    def _find_active_event_with_object_type(
        self, device: ProtectDeviceType
    ) -> Event | None:
        """Find an active event containing this sensor's object type.

        Fallback for issue #152133: last_smart_detect_event_ids may not update
        immediately when a new detection type is added to an ongoing event.
        """
        obj_type = self.entity_description.ufp_obj_type
        if obj_type is None or not isinstance(device, Camera):
            return None

        # Check known active event IDs from camera first (fast path)
        for event_id in device.last_smart_detect_event_ids.values():
            if (
                event_id
                and (event := self.data.api.bootstrap.events.get(event_id))
                and event.end is None
                and obj_type in event.smart_detect_types
            ):
                return event

        return None

    @callback
    def _async_update_device_from_protect(self, device: ProtectDeviceType) -> None:
        description = self.entity_description

        prev_event = self._event
        prev_event_end = self._event_end
        super()._async_update_device_from_protect(device)

        event = description.get_event_obj(device)
        if event is None:
            # Fallback for #152133: check active events directly
            event = self._find_active_event_with_object_type(device)

        if event:
            self._event = event
            self._event_end = event.end

        if not (
            event
            and (
                description.ufp_obj_type is None
                or description.has_matching_smart(event)
            )
            and not self._event_already_ended(prev_event, prev_event_end)
        ):
            self._set_event_done()
            return

        self._attr_is_on = True
        self._set_event_attrs(event)
        if event.end:
            self._async_event_with_immediate_end()


MODEL_DESCRIPTIONS_WITH_CLASS = (
    (_MODEL_DESCRIPTIONS, ProtectDeviceBinarySensor),
    (_MOUNTABLE_MODEL_DESCRIPTIONS, MountableProtectDeviceBinarySensor),
)


@callback
def _async_event_entities(
    data: ProtectData,
    ufp_device: ProtectAdoptableDeviceModel | None = None,
) -> list[ProtectDeviceEntity]:
    entities: list[ProtectDeviceEntity] = []
    for device in data.get_cameras() if ufp_device is None else [ufp_device]:
        entities.extend(
            ProtectEventBinarySensor(data, device, description)
            for description in EVENT_SENSORS
            if description.has_required(device)
        )
    return entities


@callback
def _async_nvr_entities(
    data: ProtectData,
) -> list[BaseProtectEntity]:
    if data.api.is_public_only:
        return []
    device = data.api.bootstrap.nvr
    if (ustorage := device.system_info.ustorage) is None:
        return []
    return [
        ProtectDiskBinarySensor(data, device, description, disk)
        for disk in ustorage.disks
        for description in DISK_SENSORS
        if disk.has_disk
    ]


@dataclasses.dataclass(frozen=True, kw_only=True)
class ProtectPublicBinaryEntityDescription(BinarySensorEntityDescription):
    """Describes a binary sensor backed by a public-API device."""

    ufp_value_fn: Callable[[PublicDeviceWithMac], bool | None]
    ufp_required_fn: Callable[[PublicDeviceWithMac], bool] = lambda _: True


PUBLIC_SENSE_SENSORS: tuple[ProtectPublicBinaryEntityDescription, ...] = (
    ProtectPublicBinaryEntityDescription(
        key=_KEY_DOOR,
        device_class=BinarySensorDeviceClass.DOOR,
        ufp_value_fn=lambda device: cast(PublicSensor, device).is_opened,
        ufp_required_fn=lambda device: cast(PublicSensor, device).is_opened is not None,
    ),
    ProtectPublicBinaryEntityDescription(
        key="motion",
        device_class=BinarySensorDeviceClass.MOTION,
        ufp_value_fn=lambda device: cast(PublicSensor, device).is_motion_detected,
    ),
    ProtectPublicBinaryEntityDescription(
        key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        ufp_value_fn=lambda device: cast(PublicSensor, device).battery_status.is_low,
    ),
)

PUBLIC_LIGHT_SENSORS: tuple[ProtectPublicBinaryEntityDescription, ...] = (
    ProtectPublicBinaryEntityDescription(
        key="dark",
        translation_key="is_dark",
        ufp_value_fn=lambda device: cast(PublicLight, device).is_dark,
    ),
    ProtectPublicBinaryEntityDescription(
        key="motion",
        device_class=BinarySensorDeviceClass.MOTION,
        ufp_value_fn=lambda device: cast(PublicLight, device).is_pir_motion_detected,
    ),
)


class ProtectPublicBinarySensor(ProtectPublicDeviceEntity, BinarySensorEntity):
    """A public-API device binary sensor for API-key-only entries."""

    entity_description: ProtectPublicBinaryEntityDescription
    _state_attrs = ("_attr_available", "_attr_is_on")

    @callback
    def _update_from_device(self, device: PublicDeviceWithMac) -> None:
        super()._update_from_device(device)
        self._attr_is_on = self.entity_description.ufp_value_fn(device)


class ProtectPublicCameraMotionSensor(BinarySensorEntity):
    """Camera motion sensor driven by the public events websocket.

    Used for API-key-only (public-only) config entries. Motion state comes
    from motion events on the public events websocket; availability follows
    the camera's connection state from the public devices websocket.
    """

    _attr_attribution = DEFAULT_ATTRIBUTION
    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, data: ProtectData, camera: PublicCamera) -> None:
        """Initialize the camera motion sensor."""
        self.data = data
        self._camera_id = camera.id
        self._camera_mac = camera.mac
        self._attr_unique_id = f"{camera.mac}_motion"
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, camera.mac)},
            identifiers={(DOMAIN, camera.mac)},
            manufacturer=DEFAULT_BRAND,
            name=camera.name,
        )
        if (via_device := data.nvr_device_identifier) is not None:
            self._attr_device_info["via_device"] = via_device
        self._attr_available = (
            data.last_update_success and camera.state is DeviceState.CONNECTED
        )
        # Unknown until the first motion event arrives.
        self._attr_is_on = None

    @callback
    def _async_motion_changed(self, is_on: bool) -> None:
        """Handle a motion event from the public events websocket."""
        if self._attr_is_on != is_on:
            self._attr_is_on = is_on
            self.async_write_ha_state()

    @callback
    def _async_camera_updated(self, camera: PublicCamera) -> None:
        """Handle a public camera WS update for availability."""
        available = (
            self.data.last_update_success and camera.state is DeviceState.CONNECTED
        )
        if self._attr_available != available:
            self._attr_available = available
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to motion events and camera updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.data.async_subscribe_public_motion(
                self._camera_id, self._async_motion_changed
            )
        )
        self.async_on_remove(
            self.data.async_subscribe_public_camera(
                self._camera_mac, self._async_camera_updated
            )
        )


# Smart detection object types exposed as binary sensors in public-only
# mode, mapped to their (entity key, translation key).
_PUBLIC_SMART_DETECT_SENSORS: tuple[tuple[SmartDetectObjectType, str, str], ...] = (
    (SmartDetectObjectType.PERSON, "smart_obj_person", "person_detected"),
    (SmartDetectObjectType.VEHICLE, "smart_obj_vehicle", "vehicle_detected"),
    (SmartDetectObjectType.ANIMAL, "smart_obj_animal", "animal_detected"),
    (SmartDetectObjectType.PACKAGE, "smart_obj_package", "package_detected"),
)


class ProtectPublicSmartDetectSensor(BinarySensorEntity):
    """Camera smart detection sensor driven by the public events websocket.

    Used for API-key-only (public-only) config entries. Detection state comes
    from smart detection events on the public events websocket; availability
    follows the camera's connection state from the public devices websocket.
    """

    _attr_attribution = DEFAULT_ATTRIBUTION
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        data: ProtectData,
        camera: PublicCamera,
        obj_type: SmartDetectObjectType,
        key: str,
        translation_key: str,
    ) -> None:
        """Initialize the smart detection sensor."""
        self.data = data
        self._camera_id = camera.id
        self._camera_mac = camera.mac
        self._obj_type = obj_type
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{camera.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(dr.CONNECTION_NETWORK_MAC, camera.mac)},
            identifiers={(DOMAIN, camera.mac)},
            manufacturer=DEFAULT_BRAND,
            name=camera.name,
        )
        if (via_device := data.nvr_device_identifier) is not None:
            self._attr_device_info["via_device"] = via_device
        self._attr_available = (
            data.last_update_success and camera.state is DeviceState.CONNECTED
        )
        # Unknown until the first smart detection event arrives.
        self._attr_is_on = None

    @callback
    def _async_event(self, event: Event) -> None:
        """Handle an event from the public events websocket."""
        if (
            event.type not in _SMART_DETECT_EVENT_TYPES
            or self._obj_type not in event.smart_detect_types
        ):
            return
        is_on = event.end is None
        if self._attr_is_on != is_on:
            self._attr_is_on = is_on
            self.async_write_ha_state()

    @callback
    def _async_camera_updated(self, camera: PublicCamera) -> None:
        """Handle a public camera WS update for availability."""
        available = (
            self.data.last_update_success and camera.state is DeviceState.CONNECTED
        )
        if self._attr_available != available:
            self._attr_available = available
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to camera events and camera updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.data.async_subscribe_public_camera_events(
                self._camera_id, self._async_event
            )
        )
        self.async_on_remove(
            self.data.async_subscribe_public_camera(
                self._camera_mac, self._async_camera_updated
            )
        )


_SMART_DETECT_EVENT_TYPES = {
    EventType.SMART_DETECT,
    EventType.SMART_DETECT_LINE,
}


@callback
def _async_setup_public_binary_sensors(
    data: ProtectData,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors from the public API for an API-key-only entry."""
    api = data.api
    if not api.has_public_bootstrap:
        return
    public_bootstrap = api.public_bootstrap
    entities: list[BinarySensorEntity] = []
    for devices, descriptions in (
        (public_bootstrap.sensors.values(), PUBLIC_SENSE_SENSORS),
        (public_bootstrap.lights.values(), PUBLIC_LIGHT_SENSORS),
    ):
        for device in devices:
            for description in descriptions:
                if not description.ufp_required_fn(device):
                    continue
                if description.key == _KEY_DOOR:
                    # Match the private integration: the contact sensor's
                    # device class follows the sensor's mount type.
                    description = dataclasses.replace(
                        description,
                        device_class=MOUNT_DEVICE_CLASS_MAP.get(
                            cast(PublicSensor, device).mount_type,
                            BinarySensorDeviceClass.DOOR,
                        ),
                    )
                entities.append(ProtectPublicBinarySensor(data, device, description))
    entities.extend(
        ProtectPublicCameraMotionSensor(data, camera)
        for camera in public_bootstrap.cameras.values()
    )
    entities.extend(
        ProtectPublicSmartDetectSensor(data, camera, obj_type, key, translation_key)
        for camera in public_bootstrap.cameras.values()
        for obj_type, key, translation_key in _PUBLIC_SMART_DETECT_SENSORS
        if obj_type in camera.feature_flags.smart_detect_types
    )
    if entities:
        async_add_entities(entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UFPConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for UniFi Protect integration."""
    data = entry.runtime_data

    if data.api.is_public_only:
        _async_setup_public_binary_sensors(data, async_add_entities)
        return

    @callback
    def _add_new_device(device: ProtectAdoptableDeviceModel) -> None:
        entities: list[BaseProtectEntity] = []
        for model_descriptions, klass in MODEL_DESCRIPTIONS_WITH_CLASS:
            entities += async_all_device_entities(
                data, klass, model_descriptions=model_descriptions, ufp_device=device
            )
        # AiPort inherits from Camera but should not create camera-specific entities
        if device.is_adopted and device.model is ModelType.CAMERA:
            entities += _async_event_entities(data, ufp_device=device)
        async_add_entities(entities)

    data.async_subscribe_adopt(_add_new_device)
    entities: list[BaseProtectEntity] = []
    for model_descriptions, klass in MODEL_DESCRIPTIONS_WITH_CLASS:
        entities += async_all_device_entities(
            data, klass, model_descriptions=model_descriptions
        )
    entities += _async_event_entities(data)
    entities += _async_nvr_entities(data)
    async_add_entities(entities)
