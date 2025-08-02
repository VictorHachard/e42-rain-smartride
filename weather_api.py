import requests
import time
import logging
from datetime import datetime, timezone
from tzlocal import get_localzone


class WeatherAPI:
    """
    WeatherAPI module for retrieving batch weather forecast data from Open-Meteo.

    Usage:

        from ride_weather_advisor.weather_api import WeatherAPI
        from datetime import datetime

        coord_map = {
            "Tournai": {"lat": 50.6071, "lon": 3.3893},
            "Mons": {"lat": 50.4541, "lon": 3.9523},
        }

        date = datetime.now()
        api = WeatherAPI()
        forecast = api.fetch_forecast(coord_map, date)

    Returned structure:

        {
            "Tournai": { datetime_obj: { ... }, ... },
            "Mons": { datetime_obj: { ... }, ... },
        }
    """
    def __init__(self):
        self.BASE_URL = "https://api.open-meteo.com/v1/forecast"
        self.MODEL = "meteofrance_arpege_europe"
        self.LOCAL_TZ = get_localzone()
        self._weather_data_cache = {}

        self.MINUTELY_FIELDS = [
            "precipitation",
            "temperature_2m",
            "wind_speed_10m",
            "wind_direction_10m",
            "weather_code"
        ]

        self.HOURLY_FIELDS = [
            "precipitation_probability"
        ]

    def fetch_forecast(self, coord_map, date: datetime):
        date_str = date.strftime("%Y-%m-%d")
        cache_key = (tuple(sorted((k, tuple(v.items())) for k, v in coord_map.items())), date_str)

        if cache_key in self._weather_data_cache:
            logging.info(f"[cache] Using cached forecast for {date_str}")
            return self._weather_data_cache[cache_key]

        raw = self._fetch_batch(coord_map, date_str)
        parsed = self._to_local_times(raw)
        self._add_print_lines(parsed)
        self._weather_data_cache[cache_key] = parsed
        return parsed

    def _build_url(self, coord_map, date_str):
        names = list(coord_map.keys())
        lats = ",".join(str(coord_map[n]["lat"]) for n in names)
        lons = ",".join(str(coord_map[n]["lon"]) for n in names)

        return (
            f"{self.BASE_URL}?"
            f"latitude={lats}&longitude={lons}"
            f"&hourly={','.join(self.HOURLY_FIELDS)}"
            f"&minutely_15={','.join(self.MINUTELY_FIELDS)}"
            f"&start_date={date_str}&end_date={date_str}"
            f"&timezone=UTC&models={self.MODEL}"
        )

    def _fetch_batch(self, coord_map, date_str, retries=3):
        url = self._build_url(coord_map, date_str)

        for attempt in range(retries):
            try:
                logging.info(f"[batch] API call attempt {attempt + 1}/{retries}: {url}")
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                raw_all = r.json()
                if isinstance(raw_all, list) and len(raw_all) == len(coord_map):
                    return dict(zip(coord_map.keys(), raw_all))
                else:
                    raise ValueError("Unexpected API response format for multi-point forecast")
            except requests.RequestException as e:
                logging.warning(f"[batch] API call failed ({attempt + 1}/{retries}): {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(1)

    def _to_local_times(self, raw_forecast):
        result = {}
        for name, raw in raw_forecast.items():
            result[name] = {}

            try:
                times_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in raw["minutely_15"]["time"]]
                times_local = [t.astimezone(self.LOCAL_TZ) for t in times_utc]

                hourly_prob_map = {}
                if raw.get("hourly") and raw["hourly"].get("time"):
                    hourly_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in raw["hourly"]["time"]]
                    hourly_local = [t.astimezone(self.LOCAL_TZ) for t in hourly_utc]
                    for field in self.HOURLY_FIELDS:
                        values = raw["hourly"].get(field, [])
                        if values:
                            hourly_prob_map[field] = {
                                t.replace(minute=0, second=0, microsecond=0): v
                                for t, v in zip(hourly_local, values)
                            }

                for i, t in enumerate(times_local):
                    entry = {
                        field: round(raw["minutely_15"].get(field, [None]*len(times_local))[i] or 0, 2)
                        for field in self.MINUTELY_FIELDS
                        if field in raw["minutely_15"] and len(raw["minutely_15"][field]) > i
                    }
                    for h_field, h_map in hourly_prob_map.items():
                        entry[h_field] = h_map.get(t.replace(minute=0, second=0, microsecond=0), 0)
                    result[name][t] = entry

            except Exception as e:
                logging.error(f"Failed to process forecast for {name}: {e}")

        return result

    def _add_print_lines(self, parsed_forecast):
        # TODO remove hardcoded values
        for name, timeline in parsed_forecast.items():
            for t, entry in timeline.items():
                try:
                    entry["print"] = (
                        f"{name}: {entry['precipitation']} mm | {entry['wind_speed_10m']} km/h | "
                        f"{entry['temperature_2m']}° | Dir {entry['wind_direction_10m']}°"
                    )
                except KeyError as e:
                    entry["print"] = f"{name}: Incomplete data at {t} ({e})"
