"""
Repositories package for virtual-store.
Provides data access layer abstraction over SQLAlchemy models.
"""
from .base import BaseRepository
from .product_repository import ProductsRepository
from .order_repository import OrdersRepository
from .settings_repository import SettingsRepository

# Additional repositories can be imported here as they are created