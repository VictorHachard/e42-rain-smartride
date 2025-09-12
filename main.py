import time
import logging
import argparse
from datetime import datetime, timedelta
from agenda_utils import get_first_and_last_class
from tzlocal import get_localzone

from vha_toolbox import seconds_to_humantime
from check_version import check_for_update
from ride_weather_advisor import RideWeatherAdvisor
from services import *

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ],
)


def parse_arguments():
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(description="E42 Rain Smartride")
    parser.add_argument('--storage-dir', type=str, required=True, help="Path to directory containing storage data.")
    parser.add_argument('--webhook', type=str, required=True, help="Discord webhook URL.")
    parser.add_argument('--mention-users', type=str, help="Comma-separated list of Discord user IDs to ping.")
    parser.add_argument('--interval', type=int, default=300, help="Interval between checks in seconds.")

    return parser.parse_args()


def create_notification_service(webhook_url, mention_users, current_version):
    if current_version:
        footer = f"E42 Rain Smartride {current_version}"
    else:
        footer = "E42 Rain Smartride"
    return NotificationService(webhook_url, mention_users, footer=footer)


def has_notification_been_sent(today):
    """Checks whether today's daily notification has already been sent."""
    status_data = config_service.get_config("file_service").load_json('daily_notification_status.json')
    return status_data.get(today, False)


def update_notification_status(today, status=True):
    """Records in a file that today's notification has been sent."""
    file_service = config_service.get_config("file_service")
    status_data = file_service.load_json('daily_notification_status.json')
    status_data[today] = status
    file_service.save_json('daily_notification_status.json', status_data)


if __name__ == "__main__":
    logging.info("Starting E42 Rain Smartride")
    logging.info(f"Local timezone: {get_localzone()}")
    update = check_for_update()
    
    args = parse_arguments()
    config_service = ConfigurationService()
    config_service.load_from_parser(args)

    interval = config_service.get_config("interval")
    discord_webhook_url = config_service.get_config("discord_webhook_url")
    mention_users = config_service.get_config("mention_users")

    current_version = update if isinstance(update, str) else update[0] if isinstance(update, tuple) else None
    config_service.set_config("notification_service", create_notification_service(discord_webhook_url, mention_users, current_version))
    notif = config_service.get_config("notification_service")

    config_service.set_config("notification_manager", NotificationManager(notif))
    notif_manager = config_service.get_config("notification_manager")

    config_service.set_config("file_service", FileService(config_service.get_config("storage_dir")))

    notif_manager.send("system_start", fields={
        "Interval": seconds_to_humantime(interval),
    })
    if isinstance(update, tuple):
        notif_manager.send("update_available", fields={"Current Version": update[0], "Latest Version": update[1]},)
    del update, current_version
    logging.info(f"Starting checks with interval of {interval} seconds")

    while True:
        now = datetime.now()
        # Check if today's notification has already been sent
        if not has_notification_been_sent(now.date().isoformat()):
            agenda_url = "https://hehplanning2025.umons.ac.be/Telechargements/ical/Edt_M0_Pass_Info_vers_Master_Informatique.ics?version=2025.5.6&icalsecurise=08861B133B1D1B3671E24F0A0B3CDF7F38107CD1F4F05BE68F3EB400F66270D3A53470C915AD2045ABD49481A5055CA9&param=643d5b312e2e36325d2666683d3126663d3131303030"
            trip_duration_minutes = 45

            try:
                first_class, last_class = get_first_and_last_class(agenda_url, now)
            except Exception as e:
                logging.error(f"Error fetching or parsing agenda: {e}")
            if first_class and last_class:
                morning_window_start = first_class - timedelta(hours=3)
                if morning_window_start >= now:
                    leave_latest = (first_class - timedelta(minutes=trip_duration_minutes)).time()
                    morning_latest_departure = leave_latest.strftime("%H:%M")
                    evening_first_departure = last_class.strftime("%H:%M")

                    logging.info(f"First class at {first_class}, last class at {last_class}")
                    logging.info(f"Morning latest departure set to {morning_latest_departure}")
                    logging.info(f"Evening first departure set to {evening_first_departure}")

                    advisor = RideWeatherAdvisor(
                        now=now, 
                        morning_latest_departure=morning_latest_departure,
                        evening_first_departure=evening_first_departure,
                        trip_duration_minutes=trip_duration_minutes
                    )
                    advisor.run_and_notify_day()
                    update_notification_status(now.date().isoformat(), True)

