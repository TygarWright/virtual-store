from .base import BaseRepository
from models import Orders

class OrdersRepository(BaseRepository[Orders]):
    def __init__(self):
        super().__init__(Orders)

    def get_by_customer(self, customer_id: int):
        """Get orders for a specific customer."""
        return self.model.query.filter_by(customer_id=customer_id).all()

    def get_recent(self, limit: int = 10):
        """Get recent orders."""
        return self.model.query.order_by(Orders.id.desc()).limit(limit).all()