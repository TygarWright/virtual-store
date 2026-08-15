"""Shared Flask extension instances.

All framework extensions live here so blueprints can import the shared
instances without creating circular imports or duplicate extension state.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
)
csrf = CSRFProtect()
