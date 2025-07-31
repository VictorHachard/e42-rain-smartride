import os
import json


class FileService:
    # TODO: Add Cache Invalidation and Thread Safety
    def __init__(self, base_dir: str):
        """
        Initializes the FileService with a base directory where files are stored.
        """
        self.base_dir = base_dir
        self._cache = {}

    def _get_full_path(self, file_name: str) -> str:
        """
        Constructs the full path to a file within the base directory.
        """
        return os.path.join(self.base_dir, file_name)

    def save_json(self, file_name: str, data: dict) -> None:
        """
        Saves a dictionary as a JSON file.
        """
        file_path = self._get_full_path(file_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        self._cache[file_name] = data

    def load_json(self, file_name: str) -> dict:
        """
        Loads JSON data from a file.
        """
        if file_name in self._cache:
            return self._cache[file_name]
        file_path = self._get_full_path(file_name)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._cache[file_name] = data
                return data
        else:
            self._cache[file_name] = {}
            return {}
