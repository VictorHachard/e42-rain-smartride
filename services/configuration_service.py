import logging


class ConfigurationService:
    _instance = None  # Singleton instance

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigurationService, cls).__new__(cls)
            cls._instance._settings = {}  # Storage for settings
        return cls._instance

    def set_config(self, key, value):
        """Sets a configuration value."""
        self._settings[key] = value

    def get_config(self, key, default=None):
        """Retrieves a configuration value."""
        return self._settings.get(key, default)

    def get_all_configs(self):
        """Returns all configuration settings."""
        return self._settings

    def load_from_parser(self, args):
        """Loads configurations from parsed arguments."""
        # Check interval is valid
        if args.interval < 5:
            logging.error("Interval must be at least 5 seconds.")
            exit(1)

        self.set_config("storage_dir", args.storage_dir)
        self.set_config("discord_webhook_url", args.webhook)
        self.set_config("mention_users", args.mention_users.split(",") if args.mention_users else None)
        self.set_config("interval", args.interval)
        
