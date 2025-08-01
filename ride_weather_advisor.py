from datetime import datetime, timedelta, timezone
import time
import requests
import logging
from services import ConfigurationService
from wmo_codes import WMO_CODES
from tzlocal import get_localzone

class RideWeatherAdvisor:
    def __init__(self,
        mode="evening",
        morning_latest_departure="07:45",
        morning_max_early_delta_min=45,
        evening_first_departure="17:30",
        evening_max_late_delta_min=30,
        trip_duration_minutes=45,
        gear_level=-1,
        max_acceptable_rain=0.2,
        max_acceptable_wind_speed=25,
        max_tolerated_wind_with_good_dir=35,
        min_acceptable_temp=6,
        risk_score_tolerance=0.15,
        risk_score_threshold=0.5,
        banned_wmo_codes=None,
        now: datetime = None
    ):
        self.MODE = mode
        self.MORNING_LATEST_DEPARTURE = morning_latest_departure
        self.MORNING_MAX_EARLY_DELTA_MIN = morning_max_early_delta_min
        self.EVENING_FIRST_DEPARTURE = evening_first_departure
        self.EVENING_MAX_LATE_DELTA_MIN = evening_max_late_delta_min
        self.TRIP_DURATION_MINUTES = trip_duration_minutes
        self.GEAR_LEVEL = gear_level
        self.MAX_ACCEPTABLE_RAIN = max_acceptable_rain
        self.MAX_ACCEPTABLE_WIND_SPEED = max_acceptable_wind_speed
        self.MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION = max_tolerated_wind_with_good_dir
        self.MIN_ACCEPTABLE_TEMP = min_acceptable_temp
        self.RISK_SCORE_TOLERANCE = risk_score_tolerance
        self.RISK_SCORE_THRESHOLD = risk_score_threshold
        self.BANNED_WMO_CODES = banned_wmo_codes or {
            45, 48, 55, 56, 57, 65, 66, 67, 75, 77, 81, 82, 86, 95, 96, 99
        }
        self.LOCAL_TZ = get_localzone()
        self.NOW = now.astimezone(self.LOCAL_TZ) if now else datetime.now(self.LOCAL_TZ)

    def get_coords(self):
        return {
            "morning": {
                "Tournai": {"lat": 50.6071, "lon": 3.3893, "dir_min": 270, "dir_max": 360},
                "E42":     {"lat": 50.549,  "lon": 3.525,  "dir_min": 270, "dir_max": 360},
                "E42bis":  {"lat": 50.474,  "lon": 3.742,  "dir_min": 180, "dir_max": 360},
                "Mons":    {"lat": 50.4541, "lon": 3.9523, "dir_min": None, "dir_max": None},
            },
            "evening": {
                "Mons":    {"lat": 50.4541, "lon": 3.9523, "dir_min": 45, "dir_max": 135},
                "E42bis":  {"lat": 50.474,  "lon": 3.742,  "dir_min": 90, "dir_max": 180},
                "E42":     {"lat": 50.549,  "lon": 3.525,  "dir_min": 90, "dir_max": 180},
                "Tournai": {"lat": 50.6071, "lon": 3.3893, "dir_min": None, "dir_max": None},
            }
        }[self.MODE]

    def compute_risk(self, wind_speed, precip, temp, weather_code, wind_dir, coord_key):
        if weather_code in self.BANNED_WMO_CODES:
            return 1.0
        COORDS = self.get_coords()
        dir_min = COORDS[coord_key].get("dir_min")
        dir_max = COORDS[coord_key].get("dir_max")
        wind_dir_ok = dir_min is None or dir_max is None or dir_min <= wind_dir <= dir_max
        score = 0.0
        if wind_speed > self.MAX_ACCEPTABLE_WIND_SPEED:
            if not wind_dir_ok or wind_speed > self.MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION:
                score += 0.6
        if precip > self.MAX_ACCEPTABLE_RAIN:
            score += 0.6
        if temp < self.MIN_ACCEPTABLE_TEMP:
            score += 0.6
        return min(score, 1.0)

    def compute_discomfort(self, temp, precip, wind_speed, gear_level):
        ideal_temp = {0: 21, 1: 13, 2: 8}[gear_level]
        temp_penalty = abs(temp - ideal_temp) / 20
        rain_penalty = min(precip / 1.5, 1.0)
        wind_penalty = max(0, (wind_speed - 15) / 25)
        return min(temp_penalty + rain_penalty + wind_penalty, 1.0)

    def fetch_weather_data_batch(self, coord_map):
        today = self.NOW.date()
        start = today.isoformat()
        end = today.isoformat()
        result = {}

        for name, cfg in coord_map.items():
            lat = cfg["lat"]
            lon = cfg["lon"]
            url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}"
                f"&hourly=precipitation_probability"
                f"&minutely_15=precipitation,temperature_2m,wind_speed_10m,wind_direction_10m,weather_code"
                f"&start_date={start}&end_date={end}&timezone=UTC&models=meteofrance_arpege_europe"
            )

            for attempt in range(3):
                try:
                    logging.info(f"[{name}] API call attempt {attempt + 1}/3: {url}")
                    r = requests.get(url, timeout=5)
                    r.raise_for_status()
                    raw = r.json()
                    break
                except requests.RequestException as e:
                    logging.warning(f"[{name}] API call failed ({attempt + 1}/3): {e}")
                    if attempt == 2:
                        raise
                    time.sleep(1)

            times_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in raw["minutely_15"]["time"]]
            times_local = [t.astimezone(self.LOCAL_TZ) for t in times_utc]
            precip = raw["minutely_15"]["precipitation"]
            wind_speed = raw["minutely_15"]["wind_speed_10m"]
            wind_dir = raw["minutely_15"]["wind_direction_10m"]
            temp_2m = raw["minutely_15"]["temperature_2m"]
            weather_codes = raw["minutely_15"]["weather_code"]

            hourly_times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc).astimezone(self.LOCAL_TZ)
                            for t in raw["hourly"]["time"]]
            hourly_precip_prob = raw["hourly"]["precipitation_probability"]
            hour_prob_map = {
                dt.replace(minute=0, second=0, microsecond=0): p
                for dt, p in zip(hourly_times, hourly_precip_prob)
            }

            result[name] = {
                t: {
                    "precip": round(p or 0, 2),
                    "wind_speed": round(ws or 0, 1),
                    "wind_dir": round(wd or 0, 1),
                    "temp_2m": round(temp or 0, 1),
                    "precip_prob": hour_prob_map.get(t.replace(minute=0, second=0, microsecond=0), 0),
                    "weather_code": code
                }
                for t, p, ws, wd, temp, code in zip(times_local, precip, wind_speed, wind_dir, temp_2m, weather_codes)
            }

        return result

    def select_best_departure(self, candidates):
        valid = [c for c in candidates if c["risk"] <= self.RISK_SCORE_THRESHOLD]

        if not valid:
            return None

        reverse = self.MODE == "morning"
        sorted_candidates = sorted(
            valid,
            key=lambda c: (round(c["risk"] + c["discomfort"], 3), c["departure"]),
            reverse=reverse
        )

        best = sorted_candidates[0]
        best_score = best["risk"] + best["discomfort"]

        close_candidates = [
            c for c in sorted_candidates
            if abs((c["risk"] + c["discomfort"]) - best_score) <= self.RISK_SCORE_TOLERANCE
        ]

        if self.MODE == "morning":
            return max(close_candidates, key=lambda c: c["departure"])
        else:  # evening
            return min(close_candidates, key=lambda c: c["departure"])


    def run_forecast_and_notify(self):
        config = ConfigurationService()
        notify = config.get_config("notification_manager")
        COORDS = self.get_coords()

        try:
            data = self.fetch_weather_data_batch(COORDS)
        except Exception as e:
            notify.send("weather_api_error", fields={"Error": str(e)})
            return

        now = self.NOW.replace(second=0, microsecond=0)
        if now.minute % 15:
            now += timedelta(minutes=15 - now.minute % 15)

        if self.MODE == "morning":
            latest_hour, latest_minute = map(int, self.MORNING_LATEST_DEPARTURE.split(":"))
            latest_departure = now.replace(hour=latest_hour, minute=latest_minute)
            earliest_departure = latest_departure - timedelta(minutes=self.MORNING_MAX_EARLY_DELTA_MIN)
            start_time = max(now, earliest_departure)
            end_time = latest_departure
        elif self.MODE == "evening":
            earliest_hour, earliest_minute = map(int, self.EVENING_FIRST_DEPARTURE.split(":"))
            earliest_departure = now.replace(hour=earliest_hour, minute=earliest_minute)
            latest_departure = earliest_departure + timedelta(minutes=self.EVENING_MAX_LATE_DELTA_MIN)
            start_time = max(now, earliest_departure)
            end_time = latest_departure
        else:
            raise ValueError("Invalid MODE selected. Choose 'morning' or 'evening'.")

        departure_times = []
        t = start_time
        while t <= end_time:
            departure_times.append(t)
            t += timedelta(minutes=15)

        options = []

        for level in ([0, 1, 2] if self.GEAR_LEVEL == -1 else [self.GEAR_LEVEL]):
            candidates = []
            for dt in departure_times:
                segments = {name: dt + timedelta(minutes=15 * i) for i, name in enumerate(COORDS)}

                try:
                    risk_scores = []
                    discomfort_scores = []

                    for pt, t in segments.items():
                        w = data[pt][t]
                        risk_scores.append(self.compute_risk(w["wind_speed"], w["precip"], w["temp_2m"], w["weather_code"], w["wind_dir"], pt))
                        discomfort_scores.append(self.compute_discomfort(w["temp_2m"], w["precip"], w["wind_speed"], level))

                    candidates.append({
                        "departure": dt,
                        "risk": max(risk_scores),
                        "discomfort": max(discomfort_scores)
                    })
                except KeyError:
                    continue

            best = self.select_best_departure(candidates)
            if best:
                options.append({"level": level, "candidates": candidates, "best": best})

        if not options:
            notify.send("no_clear_departure_found")
            return

        # Select the overall best
        overall = min(options, key=lambda o: o["best"]["risk"] + o["best"]["discomfort"])

        fields = {}
        for c in overall["candidates"]:
            departure = c["departure"]
            arrival = departure + timedelta(minutes=self.TRIP_DURATION_MINUTES)
            dep_str = departure.strftime("%H:%M")
            arr_str = arrival.strftime("%H:%M")
            prefix = "🟢 " if c == overall["best"] else "🔴 " if c["risk"] > self.RISK_SCORE_THRESHOLD else "🟡 "

            lines = []
            for pt in COORDS:
                try:
                    w = data[pt][departure + timedelta(minutes=15 * list(COORDS).index(pt))]
                    wind_dir_ok = True
                    cfg = COORDS[pt]
                    if cfg["dir_min"] is not None and cfg["dir_max"] is not None:
                        wind_dir_ok = cfg["dir_min"] <= w["wind_dir"] <= cfg["dir_max"]

                    lines.append(
                        f"{pt}: {w['precip']} mm | {w['wind_speed']} km/h | {w['temp_2m']}° | Dir {w['wind_dir']}° " +
                        ('' if cfg["dir_min"] is None or cfg["dir_max"] is None else ('✅' if wind_dir_ok else '❌'))
                    )
                except KeyError:
                    continue

            content = "\n".join(lines)
            fields[f"{prefix}{dep_str} → {arr_str} (risk={c['risk']:.2f}, discomfort={c['discomfort']:.2f})"] = content

        worst_code = max(
            (data[pt][overall['best']['departure'] + timedelta(minutes=15 * i)]["weather_code"]
            for i, pt in enumerate(COORDS)
            if overall['best']['departure'] + timedelta(minutes=15 * i) in data[pt]),
            default=0
        )
        info = WMO_CODES.get(worst_code, {"emoji": "❓", "desc": "Unknown"})
        level_desc = {0: "summer gear", 1: "mid-season gear", 2: "winter gear"}[overall["level"]]

        forecast_date_str = self.NOW.strftime("%A %d %B %Y")
        ride_label = "Departure" if self.MODE == "morning" else "Return"

        notify.send(
            "best_departure",
            fields=fields,
            title = f"{info['emoji']} {ride_label} Forecast — {forecast_date_str}",
            description=(
                f"Here is the detailed weather analysis to help you choose the best time to ride on **{forecast_date_str}**.\n"
                f"Recommended time assumes you're wearing **{level_desc}**.\n"
                f"The most significant condition expected during the ride: **{info['desc'].capitalize()}**."
            )
        )
