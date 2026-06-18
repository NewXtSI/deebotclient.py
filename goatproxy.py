from __future__ import annotations

# Configure Windows event loop BEFORE importing asyncio or any async libraries
import sys
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # Clear any existing event loop to force creation with new policy
    try:
        asyncio.set_event_loop(None)
    except RuntimeError:
        pass

import asyncio
import contextlib
import json
import logging
import signal
import time
from typing import Any

import aiohttp
import paho.mqtt.client as mqtt

from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.commands.json.battery import GetBattery
from deebot_client.commands.json.custom import CustomCommand
from deebot_client.commands.json.life_span import GetLifeSpan
from deebot_client.commands.json.volume import GetVolume
from deebot_client.events import BatteryEvent, FirmwareEvent, LifeSpan, LifeSpanEvent, TelemetryEvent, VolumeEvent
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5
from deebot_client.device import Device

# Ecovacs credentials
ECOVACS_ACCOUNT_ID = "leerzeichen32@googlemail.com"
ECOVACS_PASSWORD = "zDv42a11"
COUNTRY = "DE"

# Local MQTT broker config
LOCAL_MQTT_HOST = "10.0.20.100"
LOCAL_MQTT_PORT = 1883
LOCAL_MQTT_USER = "admin"
LOCAL_MQTT_PASSWORD = ECOVACS_PASSWORD

TOPIC_BASE = "home/goat"
TOPIC_LWT = f"{TOPIC_BASE}/LWT"
TOPIC_BATTERY = f"{TOPIC_BASE}/battery"
TOPIC_FIRMWARE = f"{TOPIC_BASE}/firmware"
TOPIC_DEVICE_NAME = f"{TOPIC_BASE}/device/name"
TOPIC_DEVICE_DID = f"{TOPIC_BASE}/device/did"
TOPIC_DEVICE_CLASS = f"{TOPIC_BASE}/device/class"
TOPIC_DEVICE_FIRMWARE = f"{TOPIC_BASE}/device/firmware"
TOPIC_RUNTIME_INFO_BASE = f"{TOPIC_BASE}/info"
TOPIC_SLEEP = f"{TOPIC_BASE}/sleep"
TOPIC_POS_BASE = f"{TOPIC_BASE}/pos"
TOPIC_INFO_BASE = f"{TOPIC_BASE}/settings"
TOPIC_SETTINGS_VOLUME = f"{TOPIC_INFO_BASE}/volume/volume"
TOPIC_SETTINGS_VOLUME_TOTAL = f"{TOPIC_INFO_BASE}/volume/total"
TOPIC_SETTINGS_VOLUME_FALL = f"{TOPIC_INFO_BASE}/volume/fallVolume"
TOPIC_SETTINGS_VOLUME_SEARCH = f"{TOPIC_INFO_BASE}/volume/searchVolume"
TOPIC_TELEMETRY_BASE = f"{TOPIC_BASE}/telemetry"

# onFwBuryPoint sensor suffix -> (subtopic, list of fields to publish)
_BURY_POINT_MAP: dict[str, tuple[str, list[str]]] = {
    "bd_sysinfo":     ("sysinfo",     ["cpuRate", "ramRate"]),
    "bd_power":       ("power",       ["corePlateVoltage", "motorDriveVoltage", "motorVoltage", "systemVoltage"]),
    "bd_batterytemp": ("batterytemp", ["temperature"]),
    "bd_reedvoltage": ("reedvoltage", ["voltage"]),
    "bd_machine":     ("machine",     ["elevatingMotorCurrent", "leftCurrent", "leftLawnCutCurrent", "rightCurrent", "rightLawnCutCurrent"]),
    "bd_batteryinfo": ("batteryinfo", ["batteryCurrent", "batteryLevel", "batteryVoltage"]),
    "bd_coreinfo":    ("coreinfo",    ["coreTemp"]),
}

RECONNECT_DELAY_SECONDS = 5

GET_INFO_SUBCOMMANDS = [
    "getCutEfficiency",
    "getObstacleHeight",
    "getCutHeight",
    "getCutDirection",
    "getAutoCutDirection",
    "getRainDelay",
    "getAnimProtect",
    "getTimeZone",
    "getCustomCutMode",
    "getBorderSwitch",
]

RUNTIME_GET_INFO_SUBCOMMANDS = [
    "getMapState",
    "getCleanInfo",
    "getChargeState",
]

OUTPUT_ONLY_GET_INFO_SUBCOMMANDS_STATS = [
    "getStats",
    "getBreakPointStatus",
    "getChargeInfo",
    "getBattery",
    "getProtectState",
    "getNetInfo",
]

GET_POS_SUBCOMMANDS = [
    "chargePos",
    "deebotPos",
]

ADDITIONAL_OUTPUT_ONLY_COMMANDS: list[tuple[str, dict[str, Any] | list[Any] | None]] = [
    ("GetWKVer", None),
    ("getTotalStats", None),
    ("getInfo", OUTPUT_ONLY_GET_INFO_SUBCOMMANDS_STATS),
    ("getMI", None),
    ("getMoveCtrlState", None),
    ("getSchedules", None),
    ("getRelocationState", None),
    ("getScheduleLatestTask", None),
    ("getAreaSet", None),
    ("getRTK", None),
    ("getAreaParameter", None),
]


class LocalPublisher:
    def __init__(self) -> None:
        self._client = mqtt.Client(client_id="goatproxy", clean_session=True)
        self._client.username_pw_set(LOCAL_MQTT_USER, LOCAL_MQTT_PASSWORD)
        self._client.will_set(TOPIC_LWT, payload="offline", qos=1, retain=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect

        self._connected = False
        self._cache: dict[str, str] = {}

    def start(self) -> None:
        self._client.connect(LOCAL_MQTT_HOST, LOCAL_MQTT_PORT, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        try:
            if self._connected:
                self._client.publish(TOPIC_LWT, payload="offline", qos=1, retain=True)
        finally:
            self._client.loop_stop()
            self._client.disconnect()

    def publish(self, topic: str, payload: str | int) -> None:
        value = str(payload)
        self._cache[topic] = value
        if self._connected:
            self._client.publish(topic, payload=value, qos=1, retain=True)

    def _on_connect(self, client: mqtt.Client, _: Any, __: Any, rc: int) -> None:
        self._connected = rc == 0
        if not self._connected:
            logging.warning("Local MQTT connect failed with rc=%s", rc)
            return

        client.publish(TOPIC_LWT, payload="online", qos=1, retain=True)
        for topic, payload in self._cache.items():
            client.publish(topic, payload=payload, qos=1, retain=True)


def _pick_goat_device(devices: list[Any]) -> Any:
    for info in devices:
        api = info.api
        if api.get("class") == "2px96q":
            return info

    for info in devices:
        api = info.api
        name = str(api.get("deviceName", "")).lower()
        nick = str(api.get("nick", "")).lower()
        if "goat" in name or "goat" in nick:
            return info

    error_message = "No GOAT device found in mqtt device list"
    raise RuntimeError(error_message)


def _lifespan_topics(component: LifeSpan) -> tuple[str, str]:
    key = component.value.lower()
    return (
        f"{TOPIC_BASE}/lifespan/{key}/left",
        f"{TOPIC_BASE}/lifespan/{key}/total",
    )


def _publish_get_info_topics(publisher: LocalPublisher, info_data: dict[str, Any]) -> None:
    def _entry_payload(key: str) -> dict[str, Any]:
        entry = info_data.get(key)
        if not isinstance(entry, dict):
            return {}
        payload = entry.get("data")
        if not isinstance(payload, dict):
            return {}
        return payload

    def _publish_if_present(topic: str, payload: dict[str, Any], field: str) -> None:
        if field in payload and payload[field] is not None:
            publisher.publish(topic, payload[field])

    cut_eff = _entry_payload("getCutEfficiency")
    _publish_if_present(f"{TOPIC_INFO_BASE}/cutEfficiency/level", cut_eff, "level")

    obstacle_height = _entry_payload("getObstacleHeight")
    _publish_if_present(
        f"{TOPIC_INFO_BASE}/obstacleHeight/level", obstacle_height, "level"
    )

    cut_height = _entry_payload("getCutHeight")
    _publish_if_present(f"{TOPIC_INFO_BASE}/cutHeight/level", cut_height, "level")

    cut_direction = _entry_payload("getCutDirection")
    _publish_if_present(f"{TOPIC_INFO_BASE}/cutDirection/angle", cut_direction, "angle")
    _publish_if_present(f"{TOPIC_INFO_BASE}/cutDirection/set", cut_direction, "set")

    auto_cut_direction = _entry_payload("getAutoCutDirection")
    _publish_if_present(
        f"{TOPIC_INFO_BASE}/autoCutDirection/enable", auto_cut_direction, "enable"
    )

    rain_delay = _entry_payload("getRainDelay")
    _publish_if_present(f"{TOPIC_INFO_BASE}/rainDelay/enable", rain_delay, "enable")
    _publish_if_present(f"{TOPIC_INFO_BASE}/rainDelay/delay", rain_delay, "delay")

    anim_protect = _entry_payload("getAnimProtect")
    _publish_if_present(f"{TOPIC_INFO_BASE}/animProtect/enable", anim_protect, "enable")
    _publish_if_present(f"{TOPIC_INFO_BASE}/animProtect/start", anim_protect, "start")
    _publish_if_present(f"{TOPIC_INFO_BASE}/animProtect/end", anim_protect, "end")

    time_zone = _entry_payload("getTimeZone")
    _publish_if_present(f"{TOPIC_INFO_BASE}/timeZone/tzm", time_zone, "tzm")
    _publish_if_present(f"{TOPIC_INFO_BASE}/timeZone/isReset", time_zone, "isReset")
    _publish_if_present(f"{TOPIC_INFO_BASE}/timeZone/code", time_zone, "code")

    custom_cut_mode = _entry_payload("getCustomCutMode")
    _publish_if_present(
        f"{TOPIC_INFO_BASE}/customCutMode/enable", custom_cut_mode, "enable"
    )

    border_switch = _entry_payload("getBorderSwitch")
    _publish_if_present(f"{TOPIC_INFO_BASE}/borderSwitch/enable", border_switch, "enable")
    _publish_if_present(f"{TOPIC_INFO_BASE}/borderSwitch/mode", border_switch, "mode")


def _pretty_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _publish_scalar_fields(
    publisher: LocalPublisher,
    topic_prefix: str,
    payload: dict[str, Any],
) -> None:
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            publisher.publish(f"{topic_prefix}/{key}", value)


def _publish_runtime_get_info_topics(
    publisher: LocalPublisher,
    info_data: dict[str, Any],
) -> None:
    def _entry_payload(key: str) -> dict[str, Any]:
        entry = info_data.get(key)
        if not isinstance(entry, dict):
            return {}
        payload = entry.get("data")
        if not isinstance(payload, dict):
            return {}
        return payload

    map_state = _entry_payload("getMapState")
    clean_info = _entry_payload("getCleanInfo")
    charge_state = _entry_payload("getChargeState")

    _publish_scalar_fields(publisher, f"{TOPIC_RUNTIME_INFO_BASE}/map", map_state)
    _publish_scalar_fields(publisher, f"{TOPIC_RUNTIME_INFO_BASE}/mowerInfo", clean_info)
    _publish_scalar_fields(
        publisher,
        f"{TOPIC_RUNTIME_INFO_BASE}/charging",
        charge_state,
    )





def _publish_pos_topics(publisher: LocalPublisher, pos_data: dict[str, Any]) -> None:
    deebot_pos = pos_data.get("deebotPos")
    if isinstance(deebot_pos, dict):
        _publish_scalar_fields(publisher, f"{TOPIC_POS_BASE}/deebot", deebot_pos)

    if "mid" in pos_data and pos_data["mid"] is not None:
        publisher.publish(f"{TOPIC_POS_BASE}/mid", pos_data["mid"])

    charge_positions = pos_data.get("chargePos")
    if isinstance(charge_positions, list):
        publisher.publish(f"{TOPIC_POS_BASE}/chargePos/count", len(charge_positions))
        for idx, item in enumerate(charge_positions):
            if not isinstance(item, dict):
                continue
            _publish_scalar_fields(publisher, f"{TOPIC_POS_BASE}/chargePos/{idx}", item)
            if idx == 0:
                _publish_scalar_fields(publisher, f"{TOPIC_POS_BASE}/chargePos", item)

    rtk_positions = pos_data.get("rtkPos")
    if isinstance(rtk_positions, list):
        publisher.publish(f"{TOPIC_POS_BASE}/rtkPos/count", len(rtk_positions))
        for idx, item in enumerate(rtk_positions):
            if not isinstance(item, dict):
                continue
            _publish_scalar_fields(publisher, f"{TOPIC_POS_BASE}/rtkPos/{idx}", item)
            if idx == 0:
                _publish_scalar_fields(publisher, f"{TOPIC_POS_BASE}/rtkPos", item)


async def _run_single_session(publisher: LocalPublisher, stop_event: asyncio.Event) -> None:
    device_id = md5(str(time.time()))
    password_hash = md5(ECOVACS_PASSWORD)

    async with aiohttp.ClientSession() as session:
        rest_config = create_rest_config(
            session,
            device_id=device_id,
            alpha_2_country=COUNTRY,
        )
        authenticator = Authenticator(rest_config, ECOVACS_ACCOUNT_ID, password_hash)
        api_client = ApiClient(authenticator)

        devices = await api_client.get_devices()
        goat_info = _pick_goat_device(devices.mqtt)

        publisher.publish(TOPIC_DEVICE_NAME, goat_info.api.get("deviceName", ""))
        publisher.publish(TOPIC_DEVICE_DID, goat_info.api["did"])
        publisher.publish(TOPIC_DEVICE_CLASS, goat_info.api["class"])

        bot = Device(goat_info, authenticator)
        ecovacs_mqtt = MqttClient(
            create_mqtt_config(device_id=device_id, country=COUNTRY),
            authenticator,
        )
        await bot.initialize(ecovacs_mqtt)

        unsubscribers: list[Any] = []
        lifespan_totals: dict[LifeSpan, int] = {}

        async def on_battery(event: BatteryEvent) -> None:
            publisher.publish(TOPIC_BATTERY, event.value)

        async def on_firmware(event: FirmwareEvent) -> None:
            publisher.publish(TOPIC_FIRMWARE, event.version)
            publisher.publish(TOPIC_DEVICE_FIRMWARE, event.version)

        async def on_life_span(event: LifeSpanEvent) -> None:
            left_topic, total_topic = _lifespan_topics(event.type)
            publisher.publish(left_topic, event.remaining)

            total = lifespan_totals.get(event.type)
            if total is None and event.percent > 0:
                total = round((event.remaining * 100) / event.percent)
                lifespan_totals[event.type] = total

            if total is not None:
                publisher.publish(total_topic, total)

        async def on_volume(event: VolumeEvent) -> None:
            publisher.publish(TOPIC_SETTINGS_VOLUME, event.volume)
            if event.maximum is not None:
                publisher.publish(TOPIC_SETTINGS_VOLUME_TOTAL, event.maximum)
            # Publish fallVolume and searchVolume if available
            if event.fall_volume is not None:
                publisher.publish(TOPIC_SETTINGS_VOLUME_FALL, event.fall_volume)
            if event.search_volume is not None:
                publisher.publish(TOPIC_SETTINGS_VOLUME_SEARCH, event.search_volume)
            logging.info(
                "onVolume event: volume=%s maximum=%s fallVolume=%s searchVolume=%s",
                event.volume,
                event.maximum,
                event.fall_volume,
                event.search_volume,
            )

        async def on_telemetry(event: TelemetryEvent) -> None:
            """Handle raw telemetry data from onFwBuryPoint-* messages."""
            mapping = _BURY_POINT_MAP.get(event.sensor_type)
            if mapping:
                subtopic, fields = mapping
                for field in fields:
                    if field in event.data and event.data[field] is not None:
                        publisher.publish(f"{TOPIC_TELEMETRY_BASE}/{subtopic}/{field}", event.data[field])
            else:
                logging.debug("Received telemetry for unmapped sensor type: %s", event.sensor_type)

        unsubscribers.append(bot.events.subscribe(BatteryEvent, on_battery))
        unsubscribers.append(bot.events.subscribe(FirmwareEvent, on_firmware))
        unsubscribers.append(bot.events.subscribe(LifeSpanEvent, on_life_span))
        unsubscribers.append(bot.events.subscribe(VolumeEvent, on_volume))
        unsubscribers.append(bot.events.subscribe(TelemetryEvent, on_telemetry))

        try:
            battery_response = await asyncio.wait_for(bot.execute_command(GetBattery()), timeout=10.0)
            data = battery_response.get("resp", {}).get("body", {}).get("data", {})
            if "value" in data:
                publisher.publish(TOPIC_BATTERY, int(data["value"]))

            fw_ver = battery_response.get("resp", {}).get("header", {}).get("fwVer")
            if fw_ver:
                publisher.publish(TOPIC_FIRMWARE, str(fw_ver))
                publisher.publish(TOPIC_DEVICE_FIRMWARE, str(fw_ver))

            info_response = await asyncio.wait_for(
                bot.execute_command(
                    CustomCommand("getInfo", GET_INFO_SUBCOMMANDS)
                ),
                timeout=10.0,
            )
            info_data = info_response.get("resp", {}).get("body", {}).get("data", {})

            if isinstance(info_data, dict) and info_data:
                logging.info("getInfo result (%d entries):", len(info_data))
                for key in GET_INFO_SUBCOMMANDS:
                    entry = info_data.get(key)
                    if entry is None:
                        logging.info("  - %s: not present in response", key)
                        continue

                    code = entry.get("code") if isinstance(entry, dict) else None
                    msg = entry.get("msg") if isinstance(entry, dict) else None
                    payload = entry.get("data") if isinstance(entry, dict) else entry

                    logging.info(
                        "  - %s | code=%s msg=%s data=%s",
                        key,
                        code,
                        msg,
                        json.dumps(payload, ensure_ascii=False),
                    )

                _publish_get_info_topics(publisher, info_data)
            else:
                logging.warning("getInfo returned no data: %s", info_response)

            try:
                volume_response = await asyncio.wait_for(bot.execute_command(GetVolume()), timeout=10.0)
                volume_data = (
                    volume_response.get("resp", {})
                    .get("body", {})
                    .get("data", {})
                )
                if isinstance(volume_data, dict):
                    if "volume" in volume_data:
                        publisher.publish(TOPIC_SETTINGS_VOLUME, int(volume_data["volume"]))
                    if "total" in volume_data:
                        publisher.publish(
                            TOPIC_SETTINGS_VOLUME_TOTAL,
                            int(volume_data["total"]),
                        )
                    if "fallVolume" in volume_data:
                        publisher.publish(
                            TOPIC_SETTINGS_VOLUME_FALL,
                            int(volume_data["fallVolume"]),
                        )
                    if "searchVolume" in volume_data:
                        publisher.publish(
                            TOPIC_SETTINGS_VOLUME_SEARCH,
                            int(volume_data["searchVolume"]),
                        )
                    logging.info(
                        "volume result: volume=%s total=%s fallVolume=%s searchVolume=%s",
                        volume_data.get("volume"),
                        volume_data.get("total"),
                        volume_data.get("fallVolume"),
                        volume_data.get("searchVolume"),
                    )
                else:
                    logging.warning("getVolume returned unexpected data: %s", volume_data)
            except Exception:
                logging.exception("getVolume failed")

            try:
                runtime_info_response = await asyncio.wait_for(
                    bot.execute_command(
                        CustomCommand("getInfo", RUNTIME_GET_INFO_SUBCOMMANDS)
                    ),
                    timeout=10.0,
                )
                runtime_info_data = (
                    runtime_info_response.get("resp", {})
                    .get("body", {})
                    .get("data", {})
                )
                if isinstance(runtime_info_data, dict):
                    _publish_runtime_get_info_topics(publisher, runtime_info_data)
                    logging.info(
                        "runtime getInfo mapped: %s",
                        _pretty_json(runtime_info_data),
                    )
                else:
                    logging.warning(
                        "runtime getInfo returned unexpected data: %s",
                        runtime_info_data,
                    )
            except Exception:
                logging.exception("runtime getInfo failed")

            try:
                sleep_response = await asyncio.wait_for(
                    bot.execute_command(CustomCommand("getSleep")),
                    timeout=10.0,
                )
                sleep_data = sleep_response.get("resp", {}).get("body", {}).get("data", {})
                if isinstance(sleep_data, dict) and "enable" in sleep_data:
                    publisher.publish(TOPIC_SLEEP, int(sleep_data["enable"]))
                    logging.info("sleep mapped: %s", sleep_data.get("enable"))
                else:
                    logging.warning("getSleep returned unexpected data: %s", sleep_data)
            except Exception:
                logging.exception("getSleep failed")

            try:
                pos_response = await asyncio.wait_for(
                    bot.execute_command(
                        CustomCommand("getPos", GET_POS_SUBCOMMANDS)
                    ),
                    timeout=10.0,
                )
                pos_data = pos_response.get("resp", {}).get("body", {}).get("data", {})
                if isinstance(pos_data, dict):
                    _publish_pos_topics(publisher, pos_data)
                    logging.info("pos mapped: %s", _pretty_json(pos_data))
                else:
                    logging.warning("getPos returned unexpected data: %s", pos_data)
            except Exception:
                logging.exception("getPos failed")

            for command_name, command_args in ADDITIONAL_OUTPUT_ONLY_COMMANDS:
                try:
                    response = await asyncio.wait_for(
                        bot.execute_command(
                            CustomCommand(command_name, command_args)
                        ),
                        timeout=10.0,
                    )
                    body = response.get("resp", {}).get("body", {})
                    code = body.get("code")
                    msg = body.get("msg")
                    data = body.get("data")
                    logging.info(
                        "output-only %s | code=%s msg=%s data=%s",
                        command_name,
                        code,
                        msg,
                        _pretty_json(data),
                    )
                except Exception:
                    logging.exception("output-only command failed: %s", command_name)

            life_span_cap = goat_info.static.capabilities.life_span
            if life_span_cap and life_span_cap.types:
                life_span_response = await asyncio.wait_for(
                    bot.execute_command(
                        GetLifeSpan(list(life_span_cap.types))
                    ),
                    timeout=10.0,
                )
                components = (
                    life_span_response.get("resp", {})
                    .get("body", {})
                    .get("data", [])
                )
                for component in components:
                    try:
                        component_type = LifeSpan(component["type"])
                        left = int(component["left"])
                        total = int(component["total"])
                    except (KeyError, ValueError, TypeError):
                        continue

                    lifespan_totals[component_type] = total
                    left_topic, total_topic = _lifespan_topics(component_type)
                    publisher.publish(left_topic, left)
                    publisher.publish(total_topic, total)

            await stop_event.wait()
        finally:
            for unsub in unsubscribers:
                unsub()
            await bot.teardown()
            await ecovacs_mqtt.disconnect()


async def run_proxy() -> None:
    logging.basicConfig(level=logging.DEBUG)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    stop_event = asyncio.Event()

    def stop_handler() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_handler)

    publisher = LocalPublisher()
    publisher.start()

    try:
        while not stop_event.is_set():
            try:
                await _run_single_session(publisher, stop_event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception(
                    "GOAT session aborted. Reconnecting in %s seconds...",
                    RECONNECT_DELAY_SECONDS,
                )
                if stop_event.is_set():
                    break
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
    finally:
        publisher.stop()


if __name__ == "__main__":
    asyncio.run(run_proxy())
