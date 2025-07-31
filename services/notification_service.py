import logging
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
    def __init__(self, notification_service):
        """
        :param notification_service: An instance of NotificationService (your notification sender)
        """
        self.notif_service = notification_service
        self.templates = {
            "system_start": {
                "title": "E42 Rain Smartride Started",
                "description": "The E42 rain smartride has started successfully.",
                "color": "#0dcaf0",
                "mention_user": False,
            },
            "update_available": {
                "title": "New Version Available",
                "description": "A new version of the E42 rain smartride is available. Please update.",
                "color": "#ffc107",
                "mention_user": False,
            },
            "best_departure_rain_check": {
                "title": "Optimal Departure Forecast",
                "description": "Here is the detailed weather analysis to help you choose the best time to ride today.",
                "color": "#0dcaf0",
                "mention_user": False,
            },
            "weather_api_error": {
                "title": "API Check Failed",
                "description": "Error fetching API data.",
                "color": "#dc3545",
                "mention_user": False,
            },
        }

    def send(self, key, url=None, fields=None):
        """
        Sends a notification using the template identified by `key`.
        """
        template = self.templates.get(key)
        if not template:
            raise ValueError(f"Notification template not found for key '{key}'")

        self.notif_service.send(
            title=template["title"],
            description=template["description"],
            url=url,
            fields=fields or {},
            color=template.get("color", "#0dcaf0"),
            mention_user=template.get("mention_user", True),
        )
