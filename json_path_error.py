class JSONPathError(Exception):
    """Custom exception raised when an invalid JSON path is provided."""
    def __init__(self, path, message="Invalid JSON path"):
        self.path = path
        self.message = f"{message}: '{path}'"
        super().__init__(self.message)
