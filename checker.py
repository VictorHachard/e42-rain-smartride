from datetime import datetime, timedelta, timezone
import requests
import logging
from services import ConfigurationService
from tzlocal import get_localzone

LOCAL_TZ = get_localzone()

# --- Parameters ---
PRECIP_COMPARISON_TOLERANCE = 0.2  # mm
MAX_ACCEPTABLE_RAIN_FOR_RIDING = 1.0  # mm
MAX_ACCEPTABLE_WIND_SPEED = 25  # km/h
MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION = 40  # km/h

# --- Configuration by point ---
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


# --- Fetch all data in one call ---
def fetch_weather_data_batch(coord_map):
    today = datetime.now().date()
    start = today.isoformat()
    end = today.isoformat()

    result = {}

    for name, cfg in coord_map.items():
        lat = cfg["lat"]
        lon = cfg["lon"]
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&minutely_15=precipitation,wind_speed_10m,wind_direction_10m"
            f"&start_date={start}&end_date={end}&timezone=UTC"
        )
        logging.info(f"[{name}] API call: {url}")
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        raw = r.json()

        times_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in raw["minutely_15"]["time"]]
        times_local = [t.astimezone(LOCAL_TZ) for t in times_utc]

        precip = raw["minutely_15"]["precipitation"]
        wind_speed = raw["minutely_15"]["wind_speed_10m"]
        wind_dir = raw["minutely_15"]["wind_direction_10m"]

        result[name] = {
            t: {
                "precip": round(p or 0, 2),
                "wind_speed": round(ws or 0, 1),
                "wind_dir": round(wd or 0, 1)
            }
            for t, p, ws, wd in zip(times_local, precip, wind_speed, wind_dir)
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

    now = datetime.now(LOCAL_TZ).replace(hour=13, minute=45, second=0, microsecond=0)
    if now.minute % 15 != 0:
        now += timedelta(minutes=15 - (now.minute % 15))

    latest_departure = now.replace(hour=17, minute=45)
    if now > latest_departure:
        notification_manager.send("too_late_today", fields={
            "Message": "Too late to leave before 07:45 today."
        })
        return

    departure_slots = []
    t = now
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
            exceeds_rain = False
            exceeds_wind = False
            bad_wind_direction = False

            for point, moment in points.items():
                entry = data[point][moment]
                rain = entry["precip"]
                wind_speed = entry["wind_speed"]
                wind_dir = entry["wind_dir"]
                cfg = COORDS[point]

                rain_values[point] = rain
                wind_values[point] = (wind_speed, wind_dir)

                if rain > MAX_ACCEPTABLE_RAIN_FOR_RIDING:
                    exceeds_rain = True

                dir_min, dir_max = cfg["dir_min"], cfg["dir_max"]
                is_direction_ok = True
                if dir_min is not None and dir_max is not None:
                    is_direction_ok = dir_min <= wind_dir <= dir_max
                    if not is_direction_ok:
                        bad_wind_direction = True

                if wind_speed > MAX_ACCEPTABLE_WIND_SPEED:
                    if not is_direction_ok or wind_speed > MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION:
                        exceeds_wind = True

            avg_precip = round(sum(rain_values.values()) / len(rain_values), 2)

            candidates.append({
                "departure": departure_time,
                "rain": rain_values,
                "wind": wind_values,
                "avg": avg_precip,
                "exceeds_rain": exceeds_rain,
                "exceeds_wind": exceeds_wind,
                "bad_wind_dir": bad_wind_direction
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
    for i, c in enumerate(candidates):
        if c["exceeds_rain"] or c["exceeds_wind"]:
            continue
        if c["avg"] < best_score - PRECIP_COMPARISON_TOLERANCE:
            best_score = c["avg"]
            best_index = i
        elif abs(c["avg"] - best_score) <= PRECIP_COMPARISON_TOLERANCE:
            if best_index is None or c["departure"] > candidates[best_index]["departure"]:
                best_index = i

    # Build Discord fields
    fields = {}
    if best_index is not None:
        best_departure_str = candidates[best_index]["departure"].strftime("%H:%M")
        fields["Recommended departure"] = f"**{best_departure_str}**"
    else:
        fields["Recommended departure"] = "None found"

    for i, c in enumerate(candidates):
        departure_str = c["departure"].strftime("%H:%M")
        lines = []
        for j, pt in enumerate(COORDS.keys()):
            rain = c["rain"][pt]
            wind_speed, wind_dir = c["wind"][pt]
            line = f"{rain} mm | {wind_speed} km/h"
            lines.append(line)

        content = " → ".join(lines)

        prefix = ""
        if c["exceeds_rain"] or c["exceeds_wind"]:
            prefix = "🔴 "
        elif i == best_index:
            prefix = "🟢 "
        else:
            prefix = "🕒 "

        fields[f"{prefix}{departure_str}"] = content

    notification_manager.send(
        "best_departure_rain_check",
        fields=fields
    )
