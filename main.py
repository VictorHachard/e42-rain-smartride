import time
import logging
import argparse
from datetime import datetime, timedelta
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


def send_daily_discord_notification(config_service):
    """
    if it hasn't been sent yet. Expects the daily log file to be stored as 'daily_log.json'
    in the storage directory.
    """
    file_service = config_service.get_config("file_service")
    
    today = (datetime.now()).strftime('%Y-%m-%d')

    if has_notification_been_sent(today):
        logging.info(f"Daily notification for {today} has already been sent.")
        return


    
        logging.info(f"Daily notification sent for {today}")
    update_notification_status(today, status=True)


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
    
    advisor = RideWeatherAdvisor(mode="morning", now=datetime(2025, 8, 2, 6, 0))
    advisor.run_forecast_and_notify()
    advisor = RideWeatherAdvisor(mode="evening", now=datetime(2025, 8, 2, 6, 0))
    advisor.run_forecast_and_notify()

    #while True:
    #rain_forecast_and_notify()
    #time.sleep(interval)

