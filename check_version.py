import os

import requests
import logging


def get_current_image_version():
    """Get the currently running Docker image version from environment variable."""
    # Try to get version from environment variable (set in Dockerfile)
    image_tag = os.getenv("IMAGE_TAG")
    if image_tag:
        logging.info(f"Current image version: {image_tag}")
        return image_tag
    else:
        logging.warning("Could not retrieve current image version")
        return None


def get_latest_github_tag():
    """Fetch the latest Git tag from the public GitHub repository using the API."""
    url = f"https://api.github.com/repos/VictorHachard/e42-rain-smartride/tags"

    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()

        tags = response.json()

        if not tags:
            logging.warning("No tags found in the repository.")
            return None

        latest_tag = tags[0]['name']
        logging.info(f"Latest GitHub tag: {latest_tag}")
        return latest_tag
    except Exception as e:
        logging.error(f"Failed to fetch latest GitHub tag: {e}")
        return None


def check_for_update():
    current_version = get_current_image_version()
    latest_version = get_latest_github_tag()

    if current_version and latest_version:
        if current_version != latest_version:
            logging.info(f"New version available: {latest_version}. Please update")
            return current_version, latest_version
        else:
            logging.info("You are using the latest version")
            return current_version
    else:
        logging.warning("Could not verify version information")
        return None
