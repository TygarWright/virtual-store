"""Application service/provider registry.

The registry is intentionally small: it provides dependency injection at the
application boundary without introducing a full container framework.
"""
from dataclasses import dataclass

from providers import (
    FirebaseAuthProvider,
    LocalStorageProvider,
    SimpleSearchProvider,
    VirtualStoreEmailProvider,
)
from payment.gateways import get_payment_gateway


@dataclass(frozen=True)
class ServiceContainer:
    payment_gateway: object
    notifications: object
    auth: object
    storage: object
    search: object


def build_service_container(config_module):
    gateway_name = "test" if str(getattr(config_module, "ALLOW_TEST_GATEWAY", False)).lower() == "true" else "razorpay"
    payment_config = {
        name: getattr(config_module, name)
        for name in (
            "RAZORPAY_KEY_ID",
            "RAZORPAY_KEY_SECRET",
            "RAZORPAY_WEBHOOK_SECRET",
        )
        if hasattr(config_module, name)
    }
    return ServiceContainer(
        payment_gateway=get_payment_gateway(gateway_name, payment_config),
        notifications=VirtualStoreEmailProvider(),
        auth=FirebaseAuthProvider(),
        storage=LocalStorageProvider(),
        search=SimpleSearchProvider(),
    )
