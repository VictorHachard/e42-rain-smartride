from datetime import datetime, timedelta, timezone
import time
import requests
import logging
from services import ConfigurationService
from tzlocal import get_localzone

WMO_CODES = {
    0:  {"emoji": "☀️",  "desc": "Clear sky"},
    1:  {"emoji": "🌤️", "desc": "Mainly clear"},
    2:  {"emoji": "⛅",  "desc": "Partly cloudy"},
    3:  {"emoji": "☁️",  "desc": "Overcast"},
    45: {"emoji": "🌫️", "desc": "Fog"},
    48: {"emoji": "🌫️❄️", "desc": "Depositing rime fog"},
    51: {"emoji": "🌦️", "desc": "Drizzle (light)"},
    53: {"emoji": "🌦️", "desc": "Drizzle (moderate)"},
    55: {"emoji": "🌧️", "desc": "Drizzle (dense)"},
    56: {"emoji": "🌧️❄️", "desc": "Freezing drizzle (light)"},
    57: {"emoji": "🌧️❄️", "desc": "Freezing drizzle (dense)"},
    61: {"emoji": "🌧️", "desc": "Rain (slight)"},
    63: {"emoji": "🌧️", "desc": "Rain (moderate)"},
    65: {"emoji": "🌧️", "desc": "Rain (heavy)"},
    66: {"emoji": "🌧️❄️", "desc": "Freezing rain (light)"},
    67: {"emoji": "🌧️❄️", "desc": "Freezing rain (heavy)"},
    71: {"emoji": "🌨️", "desc": "Snowfall (slight)"},
    73: {"emoji": "🌨️", "desc": "Snowfall (moderate)"},
    75: {"emoji": "🌨️", "desc": "Snowfall (heavy)"},
    77: {"emoji": "❄️",  "desc": "Snow grains"},
    80: {"emoji": "🌦️", "desc": "Rain showers (slight)"},
    81: {"emoji": "🌧️", "desc": "Rain showers (moderate)"},
    82: {"emoji": "🌧️🌩️", "desc": "Rain showers (violent)"},
    85: {"emoji": "🌨️", "desc": "Snow showers (slight)"},
    86: {"emoji": "🌨️", "desc": "Snow showers (heavy)"},
    95: {"emoji": "⛈️", "desc": "Thunderstorm (slight or moderate)"},
    96: {"emoji": "⛈️🌨️", "desc": "Thunderstorm with slight hail"},
    99: {"emoji": "⛈️🌨️", "desc": "Thunderstorm with heavy hail"}
}

LOCAL_TZ = get_localzone()

# --- Parameters ---

# Maximum difference between two risk scores to consider them equivalent.
# If two candidates have a similar risk (within this tolerance), the later one is preferred.
RISK_SCORE_TOLERANCE = 0.15

# Hard limit: any departure with a risk score above this value will be rejected.
RISK_SCORE_THRESHOLD = 0.5

# Avoid recommending a departure too early compared to latest arrival time.
# This limits how far in advance the recommended time can be (in minutes).
MAX_EARLY_DEPARTURE_DELTA_MINUTES = 180

MAX_ACCEPTABLE_RAIN = 0.2  # mm/15m
MAX_ACCEPTABLE_WIND_SPEED = 25  # km/h
MIN_ACCEPTABLE_TEMP = 10  # Celsius
MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION = 35  # km/h
BANNED_WMO_CODES = {
    45, 48,           # Fog and depositing rime fog (visibility hazard)
    55, 56, 57,       # Dense drizzle and freezing drizzle
    65, 66, 67,       # Heavy rain, freezing rain (dangerous traction)
    75, 77,           # Heavy snowfall, snow grains
    81, 82,           # Violent rain showers
    86,               # Heavy snow showers
    95, 96, 99        # Thunderstorms, with or without hail
}

RAIN_WEIGHT = 0.70
PROB_WEIGHT = 0.30

# --- Configuration by point ---
LATEST_DEPARTURE = "07:45"
COORDS = {
    "Tournai": {
        "lat": 50.6071,
        "lon": 3.3893,
        "dir_min": 225,
        "dir_max": 315  # NW to SW
    },
    "E42": {
        "lat": 50.549,
        "lon": 3.525,
        "dir_min": 225,
        "dir_max": 315  # NW to SW
    },
    "E42bis": {
        "lat": 50.474,
        "lon": 3.742,
        "dir_min": 180,
        "dir_max": 360  # W to E
    },
    "Mons": {
        "lat": 50.4541,
        "lon": 3.9523,
        "dir_min": None,
        "dir_max": None
    }
}
TRIP_DURATION_MINUTES = 15 * (len(COORDS) - 1)

# --- Fetch all data in one call ---
def fetch_weather_data_batch(coord_map):
    today = datetime.now(LOCAL_TZ).replace(month=8, day=1, hour=2, minute=0, second=0, microsecond=0).date()
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
                logging.info(f"[{name}] API call (try {attempt+1}/3): {url}")
                r = requests.get(url, timeout=5)
                r.raise_for_status()
                raw = r.json()
                break
            except (requests.RequestException, requests.Timeout) as e:
                logging.warning(f"[{name}] API call failed (try {attempt+1}/3): {e}")
                if attempt == 2:
                    raise  # re-raise after last attempt
                time.sleep(1)  # brief pause before retry

        # Convert times
        times_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in raw["minutely_15"]["time"]]
        times_local = [t.astimezone(LOCAL_TZ) for t in times_utc]

        precip = raw["minutely_15"]["precipitation"]
        wind_speed = raw["minutely_15"]["wind_speed_10m"]
        wind_dir = raw["minutely_15"]["wind_direction_10m"]
        temp_2m = raw["minutely_15"]["temperature_2m"]
        weather_codes = raw["minutely_15"]["weather_code"]

        # Extract hourly precip prob
        hourly_times = [
            datetime.fromisoformat(t).replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
            for t in raw["hourly"]["time"]
        ]
        hourly_precip_prob = raw["hourly"]["precipitation_probability"]

        # Map hour -> precip_prob
        hour_prob_map = {
            dt.replace(minute=0, second=0, microsecond=0): p
            for dt, p in zip(hourly_times, hourly_precip_prob)
        }

        # Final aggregation
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


# --- Main logic ---
def rain_forecast_and_notify():
    config_service = ConfigurationService()
    notification_manager = config_service.get_config("notification_manager")

    try:
        data = fetch_weather_data_batch(COORDS)
    except Exception as e:
        notification_manager.send("weather_api_error", fields={
            "Error": str(e),
            "Point": "batch"
        })
        return

    now = datetime.now(LOCAL_TZ).replace(month=8, day=1, hour=2, minute=0, second=0, microsecond=0)
    if now.minute % 15 != 0:
        now += timedelta(minutes=15 - (now.minute % 15))

    latest_hour, latest_minute = map(int, LATEST_DEPARTURE.split(":"))
    latest_departure = now.replace(hour=latest_hour, minute=latest_minute, second=0, microsecond=0)
    earliest_departure = latest_departure - timedelta(minutes=MAX_EARLY_DEPARTURE_DELTA_MINUTES)

    departure_slots = []
    t = max(now, earliest_departure)
    while t <= latest_departure:
        departure_slots.append(t)
        t += timedelta(minutes=15)

    candidates = []
    for departure_time in departure_slots:
        points = {
            name: (departure_time + timedelta(minutes=15 * i)).astimezone(LOCAL_TZ)
            for i, name in enumerate(COORDS.keys())
        }

        try:
            rain_values = {}
            wind_values = {}
            prob_values = {}
            temp_2m_values = {}
            worst_weather_code = 0
            exceeds_rain = False
            exceeds_wind = False
            bad_wind_direction = False
            too_cold = False
            banned_weather = False

            total_risk_score = 0
            count = 0

            for point, moment in points.items():
                entry = data[point][moment]
                rain = entry["precip"]
                wind_speed = entry["wind_speed"]
                wind_dir = entry["wind_dir"]
                precip_prob = entry.get("precip_prob", 0)
                temp_2m = entry["temp_2m"]
                
                weather_code = entry["weather_code"]
                worst_weather_code = max(worst_weather_code, weather_code)

                cfg = COORDS[point]

                rain_values[point] = rain
                temp_2m_values[point] = temp_2m
                wind_values[point] = (wind_speed, wind_dir)
                prob_values[point] = precip_prob

                # --- Risk Score Based on Weights ---
                rain_ratio = rain / MAX_ACCEPTABLE_RAIN
                prob_ratio = precip_prob / 100
                risk_score = (RAIN_WEIGHT * rain_ratio) + (PROB_WEIGHT * prob_ratio)

                # Optional nonlinear bias if close to threshold
                if rain_ratio > RAIN_WEIGHT and prob_ratio > PROB_WEIGHT:
                    risk_score *= 1.1

                total_risk_score += risk_score
                count += 1

                if risk_score >= 1.0:
                    exceeds_rain = True

                # Wind check
                dir_min, dir_max = cfg["dir_min"], cfg["dir_max"]
                is_direction_ok = True
                if dir_min is not None and dir_max is not None:
                    is_direction_ok = dir_min <= wind_dir <= dir_max
                    if not is_direction_ok:
                        bad_wind_direction = True

                if wind_speed > MAX_ACCEPTABLE_WIND_SPEED:
                    if not is_direction_ok or wind_speed > MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION:
                        exceeds_wind = True

                # Temperature check
                if temp_2m <= MIN_ACCEPTABLE_TEMP:
                    too_cold = True

            avg_precip = round(sum(rain_values.values()) / len(rain_values), 2)
            avg_prob = round(sum(prob_values.values()) / len(prob_values), 1)
            avg_risk_score = round(total_risk_score / count, 2)
            banned_weather = worst_weather_code in BANNED_WMO_CODES

            candidates.append({
                "departure": departure_time,
                "rain": rain_values,
                "wind": wind_values,
                "prob": prob_values,
                "temp_2m": temp_2m_values,
                "avg": avg_precip,
                "avg_prob": avg_prob,
                "avg_rain_risk": avg_risk_score,
                "exceeds_rain": exceeds_rain,
                "exceeds_wind": exceeds_wind,
                "too_cold": too_cold,
                "bad_wind_dir": bad_wind_direction,
                "worst_weather_code": worst_weather_code,
                "banned_weather": banned_weather
            })

        except KeyError:
            continue

    if not candidates:
        notification_manager.send("no_clear_departure_found", fields={
            "Message": "No departure slot found in the time window."
        })
        return

    # Select best
    best_index = None
    best_score = float("inf")

    # Determine the latest acceptable departure
    valid_departures = [
        c["departure"] for c in candidates
        if not c["exceeds_rain"]
        and not c["exceeds_wind"]
        and not c["too_cold"]
        and not c["banned_weather"]
        and c["avg_rain_risk"] <= RISK_SCORE_THRESHOLD
    ]

    if not valid_departures:
        notification_manager.send("no_clear_departure_found", fields={
            "Message": "No departure slot under risk threshold."
        })
        return

    for i, c in enumerate(candidates):
        if c["exceeds_rain"] or c["exceeds_wind"] or c["too_cold"] or c["banned_weather"]:
            continue

        score = c["avg_rain_risk"]
        if score > RISK_SCORE_THRESHOLD:
            continue

        if score < best_score - RISK_SCORE_TOLERANCE:
            best_score = score
            best_index = i
        elif abs(score - best_score) <= RISK_SCORE_TOLERANCE:
            if best_index is None or c["departure"] > candidates[best_index]["departure"]:
                best_index = i

    # Build Discord fields
    fields = {}
    for i, c in enumerate(candidates):
        departure = c["departure"]
        departure_str = departure.strftime("%H:%M")
        estimated_arrival = departure + timedelta(minutes=TRIP_DURATION_MINUTES)
        estimated_arrival_str = estimated_arrival.strftime("%H:%M")
        lines = []
        for j, pt in enumerate(COORDS.keys()):
            rain = c["rain"][pt]
            wind_speed, wind_dir = c["wind"][pt]
            prob = c["prob"][pt]
            temp_2m = c["temp_2m"][pt]
            line = f"{rain} mm | {wind_speed} km/h | {temp_2m} ° | {prob}%"
            lines.append(line)

        content = " \n ".join(lines)

        prefix = ""
        if c["exceeds_rain"] or c["exceeds_wind"] or c["too_cold"] or c["banned_weather"]:
            prefix = "⛔ "
        elif c["avg_rain_risk"] > RISK_SCORE_THRESHOLD:
            prefix = "🔴 "
        elif i == best_index:
            prefix = "🟢 "
        else:
            prefix = "🕒 "

        fields[f"{prefix}{departure_str} → {estimated_arrival_str} (risk={c['avg_rain_risk']})"] = content

    info = WMO_CODES.get(candidates[best_index]["worst_weather_code"], {"emoji": "❓", "desc": "Unknown weather"})
    description = (
        "Here is the detailed weather analysis to help you choose the best time to ride today.\n"
        f"Along the suggested route, the most significant condition expected is: **{info["desc"].capitalize()}**."
    )
    title = f"{info["emoji"]} Optimal Departure Forecast"

    notification_manager.send(
        'best_departure',
        fields=fields,
        title=title,
        description=description
    )
