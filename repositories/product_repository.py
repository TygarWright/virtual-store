from .base import BaseRepository
from models import Products

class ProductsRepository(BaseRepository[Products]):
    def __init__(self):
        super().__init__(Products)

    # Example of a custom method specific to Products
    def get_active_products(self):
        """Get products that are active (if we had an active field)."""
        # Assuming we have an 'active' column; adjust as per actual model.
        return self.model.query.filter_by(active=True).all()