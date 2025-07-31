from datetime import datetime, timedelta, timezone
import requests
import logging
from services import ConfigurationService
from tzlocal import get_localzone

LOCAL_TZ = get_localzone()

# --- Parameters ---

# Maximum difference between two risk scores to consider them equivalent.
# If two candidates have a similar risk (within this tolerance), the later one is preferred.
RISK_SCORE_TOLERANCE = 0.15

# Hard limit: any departure with a risk score above this value will be rejected.
RISK_SCORE_THRESHOLD = 0.5

# Avoid recommending a departure too early compared to latest arrival time.
# This limits how far in advance the recommended time can be (in minutes).
MAX_EARLY_DEPARTURE_DELTA_MINUTES = 120

MAX_ACCEPTABLE_RAIN = 0.5  # mm
MAX_ACCEPTABLE_WIND_SPEED = 25  # km/h
MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION = 35  # km/h

RAIN_WEIGHT = 0.70
PROB_WEIGHT = 0.30

# --- Configuration by point ---
LATEST_DEPARTURE = "17:45"
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
            f"&hourly=precipitation_probability"
            f"&minutely_15=precipitation,wind_speed_10m,wind_direction_10m"
            f"&start_date={start}&end_date={end}&timezone=UTC"
        )
        logging.info(f"[{name}] API call: {url}")
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        raw = r.json()

        # Convert times
        times_utc = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in raw["minutely_15"]["time"]]
        times_local = [t.astimezone(LOCAL_TZ) for t in times_utc]

        precip = raw["minutely_15"]["precipitation"]
        wind_speed = raw["minutely_15"]["wind_speed_10m"]
        wind_dir = raw["minutely_15"]["wind_direction_10m"]

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
                "precip_prob": hour_prob_map.get(t.replace(minute=0, second=0, microsecond=0), 0)
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
            exceeds_rain = False
            exceeds_wind = False
            bad_wind_direction = False

            total_risk_score = 0
            count = 0

            for point, moment in points.items():
                entry = data[point][moment]
                rain = entry["precip"]
                wind_speed = entry["wind_speed"]
                wind_dir = entry["wind_dir"]
                precip_prob = entry.get("precip_prob", 0)

                cfg = COORDS[point]

                rain_values[point] = rain
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

            avg_precip = round(sum(rain_values.values()) / len(rain_values), 2)
            avg_prob = round(sum(prob_values.values()) / len(prob_values), 1)
            avg_risk_score = round(total_risk_score / count, 2)

            candidates.append({
                "departure": departure_time,
                "rain": rain_values,
                "wind": wind_values,
                "prob": prob_values,
                "avg": avg_precip,
                "avg_prob": avg_prob,
                "avg_rain_risk": avg_risk_score,
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

    # Determine the latest acceptable departure
    valid_departures = [
        c["departure"] for c in candidates
        if not c["exceeds_rain"]
        and not c["exceeds_wind"]
        and c["avg_rain_risk"] <= RISK_SCORE_THRESHOLD
    ]

    if not valid_departures:
        notification_manager.send("no_clear_departure_found", fields={
            "Message": "No departure slot under risk threshold."
        })
        return

    latest_acceptable_departure = max(valid_departures)

    for i, c in enumerate(candidates):
        if c["exceeds_rain"] or c["exceeds_wind"]:
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
            line = f"{rain} mm | {wind_speed} km/h | {prob}%"
            lines.append(line)

        content = " → ".join(lines)

        prefix = ""
        if c["exceeds_rain"] or c["exceeds_wind"] or c["avg_rain_risk"] > RISK_SCORE_THRESHOLD:
            prefix = "🔴 "
        elif i == best_index:
            prefix = "🟢 "
        else:
            prefix = "🕒 "

        fields[f"{prefix}{departure_str} → {estimated_arrival_str} (risk={c['avg_rain_risk']})"] = content

    notification_manager.send(
        "best_departure_rain_check",
        fields=fields
    )
