"""
Motion sensors that clear on our schedule, and devices that are not deleted on a single missing response.

Alarm.com reports a NetworX motion zone once ("Activated") and reports Idle only after a long quiet spell, so a
motion sensor read "motion" for up to an hour. SensorController.motion_clear_s turns each activation into a pulse.

A refresh that does not return a device unregisters it, and Home Assistant then deletes its entities from the
registry. With refreshes running every few minutes, one incomplete response must not be enough for that.
"""

# Importing the component runs its sys.path shim, which must happen before the bare `_pyalarmdotcomajax`
# imports below resolve; see test_sensor_websocket_state.
import custom_components.alarmdotcom  # noqa: F401, I001

import asyncio
import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _pyalarmdotcomajax.controllers.base import MISSING_REFRESHES_BEFORE_REMOVAL
from _pyalarmdotcomajax.controllers.device_catalogs import DeviceCatalogController
from _pyalarmdotcomajax.controllers.sensors import SensorController
from _pyalarmdotcomajax.events import EventBroker, EventBrokerTopic
from _pyalarmdotcomajax.models.jsonapi import Resource
from _pyalarmdotcomajax.models.sensor import SensorState
from _pyalarmdotcomajax.websocket.client import RawResourceEventMessage
from _pyalarmdotcomajax.websocket.messages import EventWSMessage

from tests.test_catalog_refresh import SENSOR_ATTRIBUTES, SYSTEM_ID

MOTION_ID = "112674692-15"
DOOR_ID = "112674692-6"
CLEAR_S = 0.05


def _resource(resource_id: str, device_type: int, state: int, description: str) -> Resource:
    attributes = copy.deepcopy(SENSOR_ATTRIBUTES) | {
        "deviceType": device_type,
        "state": state,
        "description": description,
    }
    return Resource.from_dict({"id": resource_id, "type": "devices/sensor", "attributes": attributes})


class FakeBridge:
    """A catalog whose sensor list and states the test controls."""

    def __init__(self) -> None:
        """Start with an idle motion sensor and a closed door."""
        self.events = EventBroker()
        self.request_reconcile = MagicMock()
        self.sensors = {
            MOTION_ID: (2, SensorState.IDLE.value, "Living Room Motion"),
            DOOR_ID: (1, SensorState.CLOSED.value, "Office Balcony Door"),
        }
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, _path: Any, _ids: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            data=Resource.from_dict(
                {"id": SYSTEM_ID, "type": "settings/manage-devices/device-catalog", "attributes": {}}
            ),
            included=[_resource(rid, *spec) for rid, spec in self.sensors.items()],
        )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _setup(motion_clear_s: float) -> tuple[FakeBridge, DeviceCatalogController, SensorController]:
    bridge = FakeBridge()
    catalog = DeviceCatalogController(bridge)  # type: ignore[arg-type]
    sensors = SensorController(bridge, catalog)  # type: ignore[arg-type]
    sensors.motion_clear_s = motion_clear_s
    await catalog.initialize([SYSTEM_ID])
    await sensors.initialize()
    await _settle()
    return bridge, catalog, sensors


def _activated() -> RawResourceEventMessage:
    frame = (
        '{"EventDateUtc":"2026-10-13T20:00:00.000Z","UnitId":112674692,"DeviceId":15,"EventType":15,'
        '"EventValue":1.0,"CorrelatedId":null,"QstringForExtraData":"openClosedStatusWord=activated","DeviceType":2}'
    )
    return RawResourceEventMessage(ws_message=EventWSMessage.from_json(frame))


async def test_an_activation_clears_after_the_configured_time() -> None:
    """Motion shows for motion_clear_s, not until Alarm.com's Idle an hour later."""
    _bridge, _catalog, sensors = await _setup(CLEAR_S)

    await sensors._base_handle_event(_activated())
    assert sensors[MOTION_ID].attributes.state == SensorState.ACTIVE

    await asyncio.sleep(CLEAR_S * 3)
    assert sensors[MOTION_ID].attributes.state == SensorState.IDLE


async def test_a_refresh_does_not_turn_a_cleared_motion_back_on() -> None:
    """Alarm.com still says "activated" after the clear; applying it would show motion at every poll."""
    bridge, catalog, sensors = await _setup(CLEAR_S)

    await sensors._base_handle_event(_activated())
    await asyncio.sleep(CLEAR_S * 3)
    bridge.sensors[MOTION_ID] = (2, SensorState.ACTIVE.value, "Living Room Motion")

    await catalog._refresh()
    await _settle()

    assert sensors[MOTION_ID].attributes.state == SensorState.IDLE


async def test_a_motion_active_at_startup_also_clears() -> None:
    """At startup Alarm.com may report a motion from up to an hour ago; it must not read active for that hour."""
    bridge = FakeBridge()
    bridge.sensors[MOTION_ID] = (2, SensorState.ACTIVE.value, "Living Room Motion")
    catalog = DeviceCatalogController(bridge)  # type: ignore[arg-type]
    sensors = SensorController(bridge, catalog)  # type: ignore[arg-type]
    sensors.motion_clear_s = CLEAR_S
    await catalog.initialize([SYSTEM_ID])
    await sensors.initialize()
    await _settle()
    assert sensors[MOTION_ID].attributes.state == SensorState.ACTIVE

    await asyncio.sleep(CLEAR_S * 3)
    assert sensors[MOTION_ID].attributes.state == SensorState.IDLE


async def test_without_a_clear_time_motion_follows_alarm_com() -> None:
    """0 keeps upstream behaviour: active until Alarm.com reports Idle, and refreshes apply as they are."""
    bridge, catalog, sensors = await _setup(0)

    await sensors._base_handle_event(_activated())
    await asyncio.sleep(CLEAR_S * 3)
    assert sensors[MOTION_ID].attributes.state == SensorState.ACTIVE

    bridge.sensors[MOTION_ID] = (2, SensorState.IDLE.value, "Living Room Motion")
    await catalog._refresh()
    await _settle()
    assert sensors[MOTION_ID].attributes.state == SensorState.IDLE


async def test_a_device_missing_from_one_response_is_kept() -> None:
    """Unregistering deletes the Home Assistant entities, so it waits for several misses in a row."""
    bridge, catalog, sensors = await _setup(0)
    deleted: list[str] = []
    bridge.events.subscribe(EventBrokerTopic.RESOURCE_DELETED, lambda message: deleted.append(message.id))

    door = bridge.sensors.pop(DOOR_ID)
    for _ in range(MISSING_REFRESHES_BEFORE_REMOVAL - 1):
        await catalog._refresh()
        await _settle()
    assert DOOR_ID in sensors
    assert deleted == []

    # Back again: the count starts over.
    bridge.sensors[DOOR_ID] = door
    await catalog._refresh()
    await _settle()
    bridge.sensors.pop(DOOR_ID)
    for _ in range(MISSING_REFRESHES_BEFORE_REMOVAL - 1):
        await catalog._refresh()
        await _settle()
    assert deleted == []


async def test_a_device_really_removed_from_the_account_goes() -> None:
    """Zones removed at the panel disappear from Alarm.com for good, and then from Home Assistant."""
    bridge, catalog, sensors = await _setup(0)
    deleted: list[str] = []
    bridge.events.subscribe(EventBrokerTopic.RESOURCE_DELETED, lambda message: deleted.append(message.id))

    bridge.sensors.pop(MOTION_ID)
    for _ in range(MISSING_REFRESHES_BEFORE_REMOVAL):
        await catalog._refresh()
        await _settle()

    assert MOTION_ID not in sensors
    assert deleted == [MOTION_ID]


@pytest.mark.parametrize("minutes", [0, 5])
def test_the_hub_passes_the_option_to_the_sensor_controller(minutes: int) -> None:
    """The option is in minutes; the controller works in seconds."""
    from custom_components.alarmdotcom.const import CONF_MOTION_CLEAR_MINUTES  # noqa: PLC0415
    from custom_components.alarmdotcom.hub import AlarmHub  # noqa: PLC0415

    entry = MagicMock()
    entry.data = {"username": "u", "password": "p"}
    entry.options = {CONF_MOTION_CLEAR_MINUTES: minutes}
    entry.entry_id = f"e-{minutes}"

    hub = AlarmHub(MagicMock(data={}), entry)

    assert hub.api.sensors.motion_clear_s == minutes * 60


def test_a_sensor_knows_its_subtype() -> None:
    """The dataclass default for resource_subtypes used to hide Sensor's own, so subtype was always None."""
    from _pyalarmdotcomajax.models.sensor import Sensor, SensorSubtype  # noqa: PLC0415

    assert Sensor(_resource(MOTION_ID, 2, 3, "Living Room Motion")).subtype == SensorSubtype.MOTION_SENSOR
    assert Sensor(_resource(DOOR_ID, 1, 1, "Office Balcony Door")).subtype == SensorSubtype.CONTACT_SENSOR
