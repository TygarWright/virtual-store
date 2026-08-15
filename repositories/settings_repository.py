from .base import BaseRepository
from models import Settings

class SettingsRepository(BaseRepository[Settings]):
    def __init__(self):
        super().__init__(Settings)

    def get_value(self, key: str, default=None):
        """Get a setting value by key."""
        setting = self.get_first_by(key=key)
        return setting.value if setting else default

    def set_value(self, key: str, value: str):
        """Set a setting value by key."""
        setting = self.get_first_by(key=key)
        if setting:
            setting.value = value
            db.session.commit()
            return setting
        else:
            return self.create(key=key, value=value)