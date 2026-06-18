import aiohttp
import asyncio
import logging
import sys
import time

# Configure Windows event loop BEFORE importing any async libraries
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.commands.json.clean import Clean, CleanAction
from deebot_client.commands.json.custom import CustomCommand
from deebot_client.commands.json.volume import GetVolume
from deebot_client.events import BatteryEvent
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5
from deebot_client.device import Device

device_id = md5(str(time.time()))
account_id = "leerzeichen32@googlemail.com"
password_hash = md5("zDv42a11")
country = "DE"


async def main():
  logging.basicConfig(level=logging.DEBUG)
  stop_event = asyncio.Event()

  async with aiohttp.ClientSession() as session:
    rest_config = create_rest_config(session, device_id=device_id, alpha_2_country=country)

    authenticator = Authenticator(rest_config, account_id, password_hash)
    api_client = ApiClient(authenticator)

    devices_ = await api_client.get_devices()

    bot = Device(devices_.mqtt[0], authenticator)

    mqtt_config = create_mqtt_config(device_id=device_id, country=country)
    mqtt = MqttClient(mqtt_config, authenticator)
    await bot.initialize(mqtt)

    async def on_battery(event: BatteryEvent):
      # Do stuff on battery event
      if event.value == 100:
        # Battery full
        pass

    # Subscribe for events (more events available)
    bot.events.subscribe(BatteryEvent, on_battery)

    async def read_volume(label: str) -> tuple[int, int]:
      response = await bot.execute_command(GetVolume())
      data = response["resp"]["body"]["data"]
      volume = int(data["volume"])
      maximum = int(data.get("total", 10))
      logging.info(
        "%s volume: %s/%s",
        label,
        volume,
        maximum,
      )
      return volume, maximum

    current_volume, maximum_volume = await read_volume("Current")
    target_volume = 0 if current_volume != 0 else min(1, maximum_volume)

    if target_volume == current_volume:
      target_volume = min(maximum_volume, current_volume + 1)

    logging.info("Setting volume to %s", target_volume)
    set_response = await bot.execute_command(
      CustomCommand(
        "setVolume",
        {
          "type": "sys",
          "total": maximum_volume,
          "volume": target_volume,
        },
      )
    )

    set_body = set_response.get("resp", {}).get("body", {})
    if set_body.get("code") not in (0, None):
      error_message = f"setVolume failed: {set_body}"
      raise RuntimeError(error_message)

    await read_volume("Updated")

    # Execute commands
#    await bot.execute_command(Clean(CleanAction.START))
#    await asyncio.sleep(900)  # Wait for...
#    await bot.execute_command(Charge())

    await stop_event.wait()


if __name__ == '__main__':
  asyncio.run(main())