import locale

WMO_CODES = {
    0:  {"emoji": "☀️",  "desc": {"en": "Clear sky", "fr": "Ciel dégagé"}},
    1:  {"emoji": "🌤️", "desc": {"en": "Mainly clear", "fr": "Principalement dégagé"}},
    2:  {"emoji": "⛅",  "desc": {"en": "Partly cloudy", "fr": "Partiellement nuageux"}},
    3:  {"emoji": "☁️",  "desc": {"en": "Overcast", "fr": "Couvert"}},
    45: {"emoji": "🌫️", "desc": {"en": "Fog", "fr": "Brouillard"}},
    48: {"emoji": "🌫️❄️", "desc": {"en": "Depositing rime fog", "fr": "Brouillard givrant"}},
    51: {"emoji": "🌦️", "desc": {"en": "Drizzle (light)", "fr": "Bruine (légère)"}},
    53: {"emoji": "🌦️", "desc": {"en": "Drizzle (moderate)", "fr": "Bruine (modérée)"}},
    55: {"emoji": "🌧️", "desc": {"en": "Drizzle (dense)", "fr": "Bruine (dense)"}},
    56: {"emoji": "🌧️❄️", "desc": {"en": "Freezing drizzle (light)", "fr": "Bruine verglaçante (légère)"}},
    57: {"emoji": "🌧️❄️", "desc": {"en": "Freezing drizzle (dense)", "fr": "Bruine verglaçante (dense)"}},
    61: {"emoji": "🌧️", "desc": {"en": "Rain (slight)", "fr": "Pluie (faible)"}},
    63: {"emoji": "🌧️", "desc": {"en": "Rain (moderate)", "fr": "Pluie (modérée)"}},
    65: {"emoji": "🌧️", "desc": {"en": "Rain (heavy)", "fr": "Pluie (forte)"}},
    66: {"emoji": "🌧️❄️", "desc": {"en": "Freezing rain (light)", "fr": "Pluie verglaçante (légère)"}},
    67: {"emoji": "🌧️❄️", "desc": {"en": "Freezing rain (heavy)", "fr": "Pluie verglaçante (forte)"}},
    71: {"emoji": "🌨️", "desc": {"en": "Snowfall (slight)", "fr": "Chute de neige (faible)"}},
    73: {"emoji": "🌨️", "desc": {"en": "Snowfall (moderate)", "fr": "Chute de neige (modérée)"}},
    75: {"emoji": "🌨️", "desc": {"en": "Snowfall (heavy)", "fr": "Chute de neige (forte)"}},
    77: {"emoji": "❄️",  "desc": {"en": "Snow grains", "fr": "Grains de neige"}},
    80: {"emoji": "🌦️", "desc": {"en": "Rain showers (slight)", "fr": "Averses de pluie (faibles)"}},
    81: {"emoji": "🌧️", "desc": {"en": "Rain showers (moderate)", "fr": "Averses de pluie (modérées)"}},
    82: {"emoji": "🌧️🌩️", "desc": {"en": "Rain showers (violent)", "fr": "Averses de pluie (violentes)"}},
    85: {"emoji": "🌨️", "desc": {"en": "Snow showers (slight)", "fr": "Averses de neige (faibles)"}},
    86: {"emoji": "🌨️", "desc": {"en": "Snow showers (heavy)", "fr": "Averses de neige (fortes)"}},
    95: {"emoji": "⛈️", "desc": {"en": "Thunderstorm (slight/moderate)", "fr": "Orage (léger/modéré)"}},
    96: {"emoji": "⛈️🌨️", "desc": {"en": "Thunderstorm with slight hail", "fr": "Orage avec grêle (faible)"}},
    99: {"emoji": "⛈️🌨️", "desc": {"en": "Thunderstorm with heavy hail", "fr": "Orage avec grêle (forte)"}},
}


def get_localized_wmo_codes(lang: str = None) -> dict:
    """
    Returns the entire WMO_CODES dictionary with emoji and localized description
    for each weather code based on the requested or system language.
    """
    if lang is None:
        lang = locale.getdefaultlocale()[0]
        lang = lang[:2] if lang else 'en'

    localized_dict = {}

    for code, data in WMO_CODES.items():
        desc = data["desc"].get(lang) or data["desc"].get("en") or "Unknown"
        localized_dict[code] = {
            "emoji": data["emoji"],
            "desc": desc
        }

    return localized_dict
