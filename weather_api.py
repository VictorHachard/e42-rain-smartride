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
            logging.info(f"[weather] Using cached forecast for {date_str}")
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
                logging.info(f"[weather] API call attempt {attempt + 1}/{retries}: {url}")
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                raw_all = r.json()
                if isinstance(raw_all, list) and len(raw_all) == len(coord_map):
                    return dict(zip(coord_map.keys(), raw_all))
                else:
                    raise ValueError("Unexpected API response format for multi-point forecast")
            except requests.RequestException as e:
                logging.warning(f"[weather] API call failed ({attempt + 1}/{retries}): {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(1)

    def _to_local_times(self, raw_forecast):
        result = {}
        REQUIRED = ("precipitation", "temperature_2m", "wind_speed_10m", "wind_direction_10m", "weather_code")

        for name, raw in raw_forecast.items():
            result[name] = {}
            try:
                m15 = raw.get("minutely_15") or {}
                times_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in (m15.get("time") or [])]
                times_local = [t.astimezone(self.LOCAL_TZ) for t in times_utc]

                # map hourly fields (if any) to local rounded hour
                hourly_prob_map = {}
                hourly = raw.get("hourly") or {}
                if hourly.get("time"):
                    hourly_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
                    hourly_local = [t.astimezone(self.LOCAL_TZ) for t in hourly_utc]
                    for field in self.HOURLY_FIELDS:
                        vals = hourly.get(field) or []
                        if vals:
                            hourly_prob_map[field] = {
                                t.replace(minute=0, second=0, microsecond=0): v
                                for t, v in zip(hourly_local, vals)
                            }

                def _get(field, i):
                    arr = m15.get(field)
                    if not arr or i >= len(arr):
                        return None
                    return arr[i]

                kept = dropped = 0
                for i, t in enumerate(times_local):
                    # pull raw values
                    raw_vals = {f: _get(f, i) for f in self.MINUTELY_FIELDS}

                    # reject if any required is None
                    if any(raw_vals.get(f) is None for f in REQUIRED):
                        dropped += 1
                        continue

                    # build entry (only real numbers)
                    entry = {}
                    all_ok = True
                    for f, v in raw_vals.items():
                        if isinstance(v, (int, float)):
                            entry[f] = round(float(v), 2)
                        else:
                            all_ok = False
                            break
                    if not all_ok:
                        dropped += 1
                        continue

                    # add hourly overlays
                    hkey = t.replace(minute=0, second=0, microsecond=0)
                    for hf, hmap in hourly_prob_map.items():
                        hv = hmap.get(hkey)
                        if hv is not None:
                            entry[hf] = hv

                    result[name][t] = entry
                    kept += 1

                if dropped:
                    logging.warning(f"[weather] {name}: kept {kept}, dropped {dropped} null/incomplete minutely_15 slots")

            except Exception as e:
                logging.error(f"[weather] Failed to process forecast for {name}: {e}")

        return result

    def _add_print_lines(self, parsed_forecast):
        for name, timeline in parsed_forecast.items():
            for t, entry in timeline.items():
                p = entry.get("precipitation")
                ws = entry.get("wind_speed_10m")
                te = entry.get("temperature_2m")
                wd = entry.get("wind_direction_10m")
                if None in (p, ws, te, wd):
                    entry["print"] = f"{name}: Données incomplètes à {t:%H:%M}"
                else:
                    entry["print"] = f"{name}: {p} mm | {ws} km/h | {te}° | Dir {wd}°"
