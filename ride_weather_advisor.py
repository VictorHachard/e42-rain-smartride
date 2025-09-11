from datetime import datetime, timedelta
import logging
from services import ConfigurationService
from weather_api import WeatherAPI
from wmo_codes import get_localized_wmo_codes
from tzlocal import get_localzone

class RideWeatherAdvisor:
    def __init__(self,
        mode="evening",
        morning_latest_departure="09:45",
        morning_max_early_delta_min=45,
        evening_first_departure="12:30",
        evening_max_late_delta_min=30,
        trip_duration_minutes=45,
        gear_level=-1,
        max_acceptable_rain=0.2,
        max_acceptable_wind_speed_10m=30,
        max_tolerated_wind_with_good_dir=35,
        min_acceptable_temp=6,
        risk_score_tolerance=0.15,
        risk_score_threshold=0.6,
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
        self.MAX_ACCEPTABLE_wind_speed_10m = max_acceptable_wind_speed_10m
        self.MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION = max_tolerated_wind_with_good_dir
        self.MIN_ACCEPTABLE_TEMP = min_acceptable_temp
        self.RISK_SCORE_TOLERANCE = risk_score_tolerance
        self.RISK_SCORE_THRESHOLD = risk_score_threshold
        self.BANNED_WMO_CODES = banned_wmo_codes or {
            45, 48, 55, 56, 57, 65, 66, 67, 75, 77, 81, 82, 86, 95, 96, 99
        }
        self.LOCAL_TZ = get_localzone()
        self.NOW = now.astimezone(self.LOCAL_TZ) if now else datetime.now(self.LOCAL_TZ)

        self._weather_data_cache = {}
        self.weather_API = WeatherAPI()

    def _base_kwargs(self):
        return dict(
            morning_latest_departure=self.MORNING_LATEST_DEPARTURE,
            morning_max_early_delta_min=self.MORNING_MAX_EARLY_DELTA_MIN,
            evening_first_departure=self.EVENING_FIRST_DEPARTURE,
            evening_max_late_delta_min=self.EVENING_MAX_LATE_DELTA_MIN,
            trip_duration_minutes=self.TRIP_DURATION_MINUTES,
            gear_level=self.GEAR_LEVEL,
            max_acceptable_rain=self.MAX_ACCEPTABLE_RAIN,
            max_acceptable_wind_speed_10m=self.MAX_ACCEPTABLE_wind_speed_10m,
            max_tolerated_wind_with_good_dir=self.MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION,
            min_acceptable_temp=self.MIN_ACCEPTABLE_TEMP,
            risk_score_tolerance=self.RISK_SCORE_TOLERANCE,
            risk_score_threshold=self.RISK_SCORE_THRESHOLD,
            banned_wmo_codes=self.BANNED_WMO_CODES,
            now=self.NOW,
        )

    def get_coords(self):
        return {
            "morning": {
                "Tournai": {"lat": 50.6071, "lon": 3.3893, "dir_min": 270,  "dir_max": 360},
                "E42":     {"lat": 50.549,  "lon": 3.525,  "dir_min": 270,  "dir_max": 360},
                "E42bis":  {"lat": 50.474,  "lon": 3.742,  "dir_min": 180,  "dir_max": 360},
                "Mons":    {"lat": 50.4541, "lon": 3.9523, "dir_min": None, "dir_max": None},
            },
            "evening": {
                "Mons":    {"lat": 50.4541, "lon": 3.9523, "dir_min": 45,   "dir_max": 135},
                "E42bis":  {"lat": 50.474,  "lon": 3.742,  "dir_min": 90,   "dir_max": 180},
                "E42":     {"lat": 50.549,  "lon": 3.525,  "dir_min": 90,   "dir_max": 180},
                "Tournai": {"lat": 50.6071, "lon": 3.3893, "dir_min": None, "dir_max": None},
            }
        }[self.MODE]

    def compute_risk(self, wind_speed_10m, precipitation, temperature_2m, weather_code, wind_direction_10m, coord_key):
        if weather_code in self.BANNED_WMO_CODES:
            return 1.0
        COORDS = self.get_coords()
        dir_min = COORDS[coord_key].get("dir_min")
        dir_max = COORDS[coord_key].get("dir_max")
        wind_direction_10m_ok = dir_min is None or dir_max is None or dir_min <= wind_direction_10m <= dir_max
        score = 0.0
        if wind_speed_10m > self.MAX_ACCEPTABLE_wind_speed_10m:
            if not wind_direction_10m_ok or wind_speed_10m > self.MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION:
                score += 0.6
        if precipitation > self.MAX_ACCEPTABLE_RAIN:
            score += 0.6
        if temperature_2m < self.MIN_ACCEPTABLE_TEMP:
            score += 0.6
        return min(score, 1.0)

    def compute_discomfort(self, temperature_2m, precipitation, wind_speed_10m, gear_level):
        ideal_temp = {0: 22, 1: 14, 2: 10}[gear_level]
        temp_penalty = abs(temperature_2m - ideal_temp) / 20
        rain_penalty = min(precipitation / 1.5, 1.0)
        wind_penalty = max(0, (wind_speed_10m - 15) / 30)
        return min(temp_penalty + rain_penalty + wind_penalty, 1.0)

    def select_best_departure(self, candidates):
        if not candidates:
            return None

        reverse = self.MODE == "morning"
        sorted_candidates = sorted(
            candidates,
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

    def run_forecast(self):
        config = ConfigurationService()
        notify = config.get_config("notification_manager")

        COORDS = self.get_coords()
        try:
            data = self.weather_API.fetch_forecast(COORDS, self.NOW)
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
                        risk_scores.append(self.compute_risk(w["wind_speed_10m"], w["precipitation"], w["temperature_2m"], w["weather_code"], w["wind_direction_10m"], pt))
                        discomfort_scores.append(self.compute_discomfort(w["temperature_2m"], w["precipitation"], w["wind_speed_10m"], level))

                    candidates.append({
                        "departure": dt,
                        "risk": max(risk_scores),
                        "discomfort": max(discomfort_scores),
                        "refused": max(risk_scores) > self.RISK_SCORE_THRESHOLD or max(discomfort_scores) > self.RISK_SCORE_THRESHOLD
                    })
                except KeyError:
                    continue

            best = self.select_best_departure(candidates)
            if best:
                options.append({"level": level, "candidates": candidates, "best": best})

        return {
            "data": data,
            "coords": COORDS,
            "options": options
        }

    def combine_forecasts_same_gear(self, morning_result, evening_result):
        if not morning_result["options"] or not evening_result["options"]:
            return None

        combined = []

        for option_m in morning_result["options"]:
            if option_m["best"]:
                level = option_m["level"]
                match = next((o for o in evening_result["options"] if o["level"] == level), None)
                if match and match["best"]:
                    combined.append({
                        "level": level,
                        "morning": option_m["best"],
                        "evening": match["best"],
                        "total_risk": option_m["best"]["risk"] + match["best"]["risk"],
                        "total_discomfort": option_m["best"]["discomfort"] + match["best"]["discomfort"],
                        "refused": option_m["best"]["refused"] or match["best"]["refused"],
                    })

        if not combined:
            return None

        # On prend le combo avec la somme score la plus basse
        best = min(combined, key=lambda o: (round(o["total_risk"] + o["total_discomfort"], 3)))
        return best

    def notify_forecast_summary(self, forecast_result):
        config = ConfigurationService()
        notify = config.get_config("notification_manager")

    
        level = forecast_result["level"]
        dep_m = forecast_result["morning"]["departure"]
        dep_e = forecast_result["evening"]["departure"]
        risk_m = forecast_result["morning"]["risk"]
        risk_e = forecast_result["evening"]["risk"]
        disc_m = forecast_result["morning"]["discomfort"]
        disc_e = forecast_result["evening"]["discomfort"]

        if forecast_result["refused"]:
            notify.send(
                "no_round_trip_departure",
                args={
                    "forecast_date": self.NOW,
                }
            )
            return

        level_desc = {0: "summer gear", 1: "mid-season gear", 2: "winter gear"}[level]

        notify.send(
            "round_trip_departure",
            args={
                "forecast_date": self.NOW,
                "level_desc": level_desc,
                "dep_m": dep_m.strftime('%H:%M'),
                "risk_m": round(risk_m, 2),
                "disc_m": round(disc_m, 2),
                "dep_e": dep_e.strftime('%H:%M'),
                "risk_e": round(risk_e, 2),
                "disc_e": round(disc_e, 2),
            }
        )

    def run_and_notify_day(self):
        base = self._base_kwargs()
        
        morning = RideWeatherAdvisor(mode="morning", **base)
        evening = RideWeatherAdvisor(mode="evening", **base)

        morning_result = morning.run_forecast()
        evening_result = evening.run_forecast()
        #logging.info(morning_result)
        #logging.info(evening_result)

        combo = self.combine_forecasts_same_gear(morning_result, evening_result)
        
        if combo:
            gear = combo['level']
            self.notify_forecast_summary(combo)
            morning.notify_forecast(morning_result, gear)
            evening.notify_forecast(evening_result, gear)
        
        else:
            morning.notify_forecast(morning_result)
            evening.notify_forecast(evening_result)

    def notify_forecast(self, forecast_result, gear=None):
        config = ConfigurationService()
        notify = config.get_config("notification_manager")

        data = forecast_result["data"]
        COORDS = forecast_result["coords"]
        options = forecast_result["options"]

        if not options:
            notify.send("no_clear_departure_found")
            return

        if gear == None:
            overall = min(options, key=lambda o: o["best"]["risk"] + o["best"]["discomfort"])
        else:
            overall = [o for o in options if o["level"] == gear][0]

        fields = {}
        for c in overall["candidates"]:
            departure = c["departure"]
            arrival = departure + timedelta(minutes=self.TRIP_DURATION_MINUTES)
            dep_str = departure.strftime("%H:%M")
            arr_str = arrival.strftime("%H:%M")
            prefix = (
                "🟢 " if c == overall["best"] and not c["refused"]
                else "🔴 " if c["refused"]
                else "🟡 "
            )

            lines = []
            for i, pt in enumerate(COORDS):
                try:
                    t = departure + timedelta(minutes=15 * i)
                    w = data[pt][t]
                    cfg = COORDS[pt]

                    wind_note = ""
                    if cfg["dir_min"] is not None and cfg["dir_max"] is not None:
                        if cfg["dir_min"] <= w["wind_direction_10m"] <= cfg["dir_max"]:
                            wind_note = " ✅"
                        else:
                            wind_note = " ❌"

                    lines.append(w["print"] + wind_note)
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
        info = get_localized_wmo_codes().get(worst_code, {"emoji": "❓", "desc": "Unknown"})
        level_desc = {0: "summer gear", 1: "mid-season gear", 2: "winter gear"}[overall["level"]]

        notify.send(
            "best_departure" if self.MODE == "morning" else "best_return",
            fields=fields,
            args={
                "forecast_date": self.NOW,
                "level_desc": level_desc,
                "info_emoji": info['emoji'],
                "info_desc": info['desc'].capitalize(),
            }
        )

    def llm_suggest_departure_local(self, max_candidates: int = 64):
        """
        Utilise gpt-oss-20b en local via transformers pour analyser les créneaux bruts
        et choisir le meilleur départ.
        """
        import json
        from transformers import pipeline
        import torch

        # --- 1) Construire la fenêtre temporelle ---
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

        if not departure_times:
            return None

        # --- 2) Récupérer données météo brutes ---
        COORDS = self.get_coords()
        try:
            data = self.weather_API.fetch_forecast(COORDS, self.NOW)
        except Exception:
            return None

        raw_candidates = []
        for dt in departure_times[:max_candidates]:
            segments = []
            for i, pt in enumerate(COORDS):
                ts = dt + timedelta(minutes=15 * i)
                w = data.get(pt, {}).get(ts)
                if not w:
                    continue
                segments.append({
                    "point": pt,
                    "timestamp_iso": ts.isoformat(),
                    "weather_code": w.get("weather_code"),
                    "precipitation_mm": w.get("precipitation"),
                    "temperature_c": w.get("temperature_2m"),
                    "wind_speed_10m_kmh": w.get("wind_speed_10m"),
                    "wind_direction_10m_deg": w.get("wind_direction_10m"),
                    "print": w.get("print"),
                    "dir_ok_range": {
                        "min": COORDS[pt].get("dir_min"),
                        "max": COORDS[pt].get("dir_max"),
                    },
                })
            if segments:
                raw_candidates.append({
                    "departure_iso": dt.isoformat(),
                    "segments": segments,
                })

        if not raw_candidates:
            return None

        constraints = {
            "mode": self.MODE,
            "trip_duration_minutes": self.TRIP_DURATION_MINUTES,
            "banned_wmo_codes": sorted(list(self.BANNED_WMO_CODES)),
            "max_acceptable_rain_mm_per_15min": self.MAX_ACCEPTABLE_RAIN,
            "max_acceptable_wind_speed_10m_kmh": self.MAX_ACCEPTABLE_wind_speed_10m,
            "max_tolerated_wind_with_good_dir_kmh": self.MAX_TOLERATED_WIND_WITH_GOOD_DIRECTION,
            "min_acceptable_temp_c": self.MIN_ACCEPTABLE_TEMP,
            "risk_score_threshold": self.RISK_SCORE_THRESHOLD,
            "allowed_gear_levels": [0, 1, 2] if self.GEAR_LEVEL == -1 else [self.GEAR_LEVEL],
        }

        # --- 3) Charger le modèle en local ---
        model_id = "openai/gpt-oss-20b"
        pipe = pipeline(
            "text-generation",
            model=model_id,
            torch_dtype="auto",
            device_map="auto",
        )

        # --- 4) Construire le prompt ---
        prompt = (
            "Tu es un assistant qui analyse des créneaux météo pour trajets moto.\n"
            "Voici les contraintes en JSON:\n"
            f"{json.dumps(constraints, ensure_ascii=False)}\n\n"
            "Voici les candidats en JSON:\n"
            f"{json.dumps(raw_candidates, ensure_ascii=False)}\n\n"
            "Choisis le meilleur créneau ('departure_iso'), propose un 'gear_level_suggestion', "
            "donne une explication courte ('reasoning') et 1 à 3 alternatives.\n"
            "Réponds STRICTEMENT en JSON avec ce schéma:\n"
            "{\n"
            '  "mode": "morning|evening",\n'
            '  "chosen_departure_iso": "ISO-8601",\n'
            '  "gear_level_suggestion": 0|1|2,\n'
            '  "reasoning": "texte court",\n'
            '  "alternatives": [{"departure_iso": "ISO-8601", "note": "texte"}]\n'
            "}\n"
        )

        # --- 5) Génération ---
        outputs = pipe(
            prompt,
            max_new_tokens=512,
            temperature=0.2,
        )

        content = outputs[0]["generated_text"]

        # --- 6) Parsing JSON ---
        def _safe_parse_json(txt: str):
            import re
            match = re.search(r"\{.*\}", txt, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except Exception:
                    return None
            return None

        result = _safe_parse_json(content)
        if not result:
            return None

        #logging.info(result)
        return result
