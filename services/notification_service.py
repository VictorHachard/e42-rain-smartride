import logging
import locale
import re
from datetime import date, datetime
from discord_webhook import DiscordWebhook, DiscordEmbed

# Discord embed limits
MAX_TITLE_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 4096
MAX_FIELD_COUNT = 25
MAX_FIELD_NAME_LENGTH = 256
MAX_FIELD_VALUE_LENGTH = 1024
MAX_FOOTER_TEXT_LENGTH = 2048

def smart_truncate(text, max_length, placeholder="..."):
    """
    Truncate text at the last whitespace before max_length to avoid cutting words in half.
    If no whitespace is found, it truncates exactly at max_length.
    """
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")
    if last_space != -1:
        truncated = truncated[:last_space]
    return truncated.rstrip() + placeholder

class NotificationService:
    def __init__(self, webhook_url, mention_users=None, footer="Content monitoring system"):
        """
        Initialize the NotificationService with webhook URL and optional user mentions.
        """
        self.webhook_url = webhook_url
        self.mention_users = mention_users or []
        if len(footer) > MAX_FOOTER_TEXT_LENGTH:
            logging.warning("Footer text exceeds maximum length. Truncating.")
            footer = footer[:MAX_FOOTER_TEXT_LENGTH]
        self.footer = footer
    
    def _validate_embed_content(self, title, description, fields):
        """
        Validate and enforce Discord embed limits for title, description, and fields.
        Returns the validated (and possibly smart-truncated) title, description, and fields
        as a list of (name, value) tuples.
        """
        # Validate title
        if title and len(title) > MAX_TITLE_LENGTH:
            logging.warning("Title exceeds maximum length. Truncating.")
            title = smart_truncate(title, MAX_TITLE_LENGTH)
        
        # Validate description
        if description and len(description) > MAX_DESCRIPTION_LENGTH:
            logging.warning("Description exceeds maximum length. Truncating.")
            description = smart_truncate(description, MAX_DESCRIPTION_LENGTH)
        
        validated_fields = []
        if fields:
            # Convert fields (a dict) to a list of tuples to preserve order
            for field_name, field_value in fields.items():
                if len(field_name) > MAX_FIELD_NAME_LENGTH:
                    logging.warning(f"Field name '{field_name}' exceeds maximum length. Truncating.")
                    field_name = smart_truncate(field_name, MAX_FIELD_NAME_LENGTH)
                if len(field_value) > MAX_FIELD_VALUE_LENGTH:
                    logging.warning(f"Field value for '{field_name}' exceeds maximum length. Truncating.")
                    field_value = smart_truncate(field_value, MAX_FIELD_VALUE_LENGTH)
                validated_fields.append((field_name, field_value))
        return title, description, validated_fields

    def _create_embeds(self, title, description, url, fields, color):
        """
        Create a list of DiscordEmbed objects.
        Splits fields into chunks of MAX_FIELD_COUNT. The first embed includes the provided title,
        description, URL, and color, while additional embeds use a 'Continued' title.
        """
        embeds = []
        # Split fields into chunks if fields exist; otherwise, create a single embed without fields.
        field_chunks = [fields[i:i + MAX_FIELD_COUNT] for i in range(0, len(fields), MAX_FIELD_COUNT)] if fields else [None]

        for idx, chunk in enumerate(field_chunks):
            if idx == 0:
                embed_title = title
                embed_description = description
                embed_color = color.replace("#", "") if color and color.startswith("#") else color
            else:
                embed_title = "Continued"
                embed_description = ""
                embed_color = None  # Optionally, you might want to reuse the color

            embed = DiscordEmbed(
                title=embed_title,
                description=embed_description,
                color=embed_color,
            )
            if idx == 0 and url:
                embed.set_url(url)

            if chunk:
                for field_name, field_value in chunk:
                    embed.add_embed_field(name=field_name, value=field_value, inline=False)

            embed.set_footer(text=self.footer)
            embed.set_timestamp()
            embeds.append(embed)
        return embeds

    def send(self, title, description, url=None, fields=None, color=None, mention_user=True):
        """
        Send a Discord notification using the webhook URL.
        If the number of fields exceeds Discord's limit, the fields are split across multiple embeds.
        """
        title, description, validated_fields = self._validate_embed_content(title, description, fields)

        if mention_user:
            mention_content = " ".join([f"<@{user}>" for user in self.mention_users]) if self.mention_users else ""
        else:
            mention_content = None

        try:
            webhook = DiscordWebhook(url=self.webhook_url, content=mention_content)
            embeds = self._create_embeds(title, description, url, validated_fields, color)
            for embed in embeds:
                webhook.add_embed(embed)

            response = webhook.execute()
            if response.status_code != 200:
                logging.info(f"Failed to send notification: {description}, (fields: {fields})")
            logging.info(f"Notification sent: {response}")
        except Exception as e:
            logging.error(f"Failed to send notification: {e}")
            logging.exception(e)


class NotificationManager:
    def __init__(self, notification_service, lang=None):
        """
        :param notification_service: An instance of NotificationService (your notification sender)
        """
        self.notif_service = notification_service

        if lang is None:
            lang = locale.getdefaultlocale()[0]
            self.lang = lang[:2] if lang else "en"
        else:
            self.lang = lang

        self.templates = {
            "system_start": {
                "en": {
                    "title": "E42 Rain Smartride Started",
                    "description": "The E42 rain smartride has started successfully.",
                },
                "fr": {
                    "title": "Smartride Pluie E42 Démarré",
                    "description": "Le smartride pluie E42 a démarré avec succès.",
                },
                "color": "#0dcaf0",
                "mention_user": False,
            },
            "update_available": {
                "en": {
                    "title": "New Version Available",
                    "description": "A new version of the E42 rain smartride is available. Please update.",
                },
                "fr": {
                    "title": "Nouvelle Version Disponible",
                    "description": "Une nouvelle version du smartride pluie E42 est disponible. Veuillez mettre à jour.",
                },
                "color": "#ffc107",
                "mention_user": False,
            },
            "round_trip_departure": {
                "en": {
                    "title": "Optimal Round-Trip Forecast — {forecast_date}",
                    "description": (
                        "Here is the round-trip weather forecast for **{forecast_date}**.\n"
                        "Recommended gear: **{level_desc}**.\n\n"
                        "**Departure at {dep_m}** — "
                        "Risk: {risk_m:.2f}, Discomfort: {disc_m:.2f}\n"
                        "**Return at {dep_e}** — "
                        "Risk: {risk_e:.2f}, Discomfort: {disc_e:.2f}"
                    ),
                },
                "fr": {
                    "title": "Prévision Aller-Retour Optimale — {forecast_date}",
                    "description": (
                        "Voici la prévision aller-retour pour le **{forecast_date}**.\n"
                        "Équipement recommandé : **{level_desc}**.\n\n"
                        "**Départ à {dep_m}** — "
                        "Risque : {risk_m:.2f}, Inconfort : {disc_m:.2f}\n"
                        "**Retour à {dep_e}** — "
                        "Risque : {risk_e:.2f}, Inconfort : {disc_e:.2f}"
                    ),
                },
                "color": "#20c997",
                "mention_user": False,
            },
            "no_round_trip_departure": {
                "en": {
                    "title": "🚫 No Round-Trip Forecast — {forecast_date}",
                    "description": "Today’s conditions don’t allow for a safe and comfortable round-trip.",
                },
                "fr": {
                    "title": "🚫 Aucun Aller-Retour Sûr — {forecast_date}",
                    "description": "Les conditions actuelles ne permettent pas un aller-retour confortable et sécurisé.",
                },
                "color": "#ffc107",
                "mention_user": False,
            },
            "best_departure": {
                "en": {
                    "title": "{info_emoji} Departure Forecast — {forecast_date}",
                    "description": (
                        "Here is the detailed weather analysis to help you choose the best time to ride on **{forecast_date}**.\n"
                        "Recommended time assumes you're wearing **{level_desc}**.\n"
                        "Main condition expected: **{info_desc}**."
                    ),
                },
                "fr": {
                    "title": "{info_emoji} Prévision aller — {forecast_date}",
                    "description": (
                        "Voici l’analyse météo pour le **{forecast_date}**.\n"
                        "Départ conseillé avec **{level_desc}**.\n"
                        "Condition principale : **{info_desc}**."
                    ),
                },
                "color": "#0dcaf0",
                "mention_user": False,
            },
            "best_return": {
                "en": {
                    "title": "{info_emoji} Return Forecast — {forecast_date}",
                    "description": (
                        "Here is the detailed weather analysis to help you choose the best time to ride on **{forecast_date}**.\n"
                        "Recommended time assumes you're wearing **{level_desc}**.\n"
                        "Main condition expected: **{info_desc}**."
                    ),
                },
                "fr": {
                    "title": "{info_emoji} Prévision retour — {forecast_date}",
                    "description": (
                        "Voici l’analyse météo pour le **{forecast_date}**.\n"
                        "Départ conseillé avec **{level_desc}**.\n"
                        "Condition principale : **{info_desc}**."
                    ),
                },
                "color": "#0dcaf0",
                "mention_user": False,
            },
            "no_clear_departure_found": {
                "en": {
                    "title": "No Safe Departure Found",
                    "description": "No safe departure slot available.",
                },
                "fr": {
                    "title": "Aucun Départ Sûr Disponible",
                    "description": "Aucune fenêtre de départ sûre disponible aujourd’hui.",
                },
                "color": "#ffc107",
                "mention_user": False,
            },
            "weather_api_error": {
                "en": {
                    "title": "API Check Failed",
                    "description": "Error fetching API data.",
                },
                "fr": {
                    "title": "Erreur API Météo",
                    "description": "Une erreur s’est produite lors de la récupération des données météo.",
                },
                "color": "#dc3545",
                "mention_user": False,
            },
        }
        
    def _check_required_format_keys(self, text, args):
        placeholders = set(re.findall(r"{([a-zA-Z0-9_]+)}", text))
        provided = set(args or {})
        missing = placeholders - provided
        if missing:
            raise KeyError(f"Missing template arguments: {', '.join(missing)}")

    def _format_args(self, args, lang):
        formatted = {}
        for k, v in (args or {}).items():
            if isinstance(v, (date, datetime)):
                formatted[k] = self._format_date(v, lang)
            else:
                formatted[k] = v
        return formatted

    def _format_date(self, dt, lang):
        if not isinstance(dt, (date, datetime)):
            return str(dt)

        if lang == "fr":
            jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
            mois = ["janvier", "février", "mars", "avril", "mai", "juin",
                    "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
            j = jours[dt.weekday()]
            m = mois[dt.month - 1]
            return f"{j} {dt.day} {m} {dt.year}"
        else:
            return dt.strftime("%A, %B %-d, %Y") if hasattr(dt, "strftime") else str(dt)

    def send(self, key, url=None, fields=None, lang=None, args=None):
        template = self.templates.get(key)
        if not template:
            raise ValueError(f"Notification template not found for key '{key}'")
        
        if lang == None:
            lang = self.lang

        localized = template.get(lang) or template.get("en") or {}

        fmt_args = self._format_args(args, lang)

        title_tpl = localized.get("title", "")
        description_tpl = localized.get("description", "")

        self._check_required_format_keys(title_tpl, fmt_args)
        self._check_required_format_keys(description_tpl, fmt_args)

        self.notif_service.send(
            title=title_tpl.format(**fmt_args),
            description=description_tpl.format(**fmt_args),
            url=url,
            fields=fields or {},
            color=template.get("color", "#0dcaf0"),
            mention_user=template.get("mention_user", True),
        )