"""
Payment gateway abstraction layer.
Supports multiple payment providers with a unified interface.

Amount contract
---------------
All amounts accepted and returned by this abstraction are integer values in
minor currency units. For INR, that means paise. Razorpay also expects paise,
so the gateway never performs an implicit INR * 100 conversion. Callers that
start with rupees must convert to paise before calling this layer.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any
import hashlib
import hmac
import logging
import os

logger = logging.getLogger(__name__)

_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass
class PaymentResult:
    """Result of a payment operation."""
    success: bool
    provider_payment_id: Optional[str] = None
    provider_order_id: Optional[str] = None
    provider_refund_id: Optional[str] = None
    amount: Optional[int] = None  # integer in smallest currency unit (e.g., paise)
    currency: Optional[str] = None
    status: Optional[str] = None
    error_message: Optional[str] = None
    retryable: bool = False
    raw_response: Optional[Dict[str, Any]] = None


class PaymentGateway(ABC):
    """Abstract base class for payment gateways."""

    def __init__(self, config: Dict[str, Any]):
        self.config = dict(config or {})
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the gateway is properly configured."""
        pass

    @abstractmethod
    def create_order(self, amount: int, currency: str, receipt: str,
                     notes: Optional[Dict[str, Any]] = None) -> PaymentResult:
        """
        Create a payment order.

        Args:
            amount: Amount in smallest currency unit (e.g., paise for INR).
            currency: Currency code (e.g., 'INR', 'USD').
            receipt: Merchant order reference ID.
            notes: Optional metadata.

        Returns:
            PaymentResult with provider_order_id on success.
        """
        pass

    @abstractmethod
    def verify_payment_signature(self, order_id: str, payment_id: str,
                                 signature: str) -> bool:
        """Verify a payment signature from the client."""
        pass

    @abstractmethod
    def capture_payment(self, payment_id: str,
                        amount: Optional[int] = None) -> PaymentResult:
        """Capture or verify an authorized payment."""
        pass

    @abstractmethod
    def refund_payment(self, payment_id: str,
                       amount: Optional[int] = None,
                       idempotency_key: Optional[str] = None) -> PaymentResult:
        """Refund a payment; amount is in the smallest currency unit."""
        pass

    @abstractmethod
    def get_payment_status(self, payment_id: str) -> PaymentResult:
        """Get the current status of a payment."""
        pass

    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify a webhook signature from the provider."""
        pass


class RazorpayGateway(PaymentGateway):
    """Razorpay payment gateway implementation.

    The existing ``razorpay_client`` module reads credentials from its module
    global ``config`` object. This adapter deliberately does not call helpers
    that depend on that mutable state. Every request supplies this gateway
    instance's credentials directly, making separate gateway instances safe to
    use concurrently.
    """

    def _setting(self, name: str, default: Any = "") -> Any:
        """Read instance config, falling back to the application's config.

        The fallback is read-only. No values are written to the application's
        config or to ``razorpay_client.config`` during a request.
        """
        if name in self.config:
            return self.config[name]
        try:
            import config as app_config
        except Exception:
            return default
        return getattr(app_config, name, default)

    def _auth(self):
        return (
            self._setting("RAZORPAY_KEY_ID"),
            self._setting("RAZORPAY_KEY_SECRET"),
        )

    def _api_base(self) -> str:
        try:
            import razorpay_client as rzp
            return getattr(rzp, "API_BASE", "https://api.razorpay.com/v1")
        except Exception:
            return "https://api.razorpay.com/v1"

    @staticmethod
    def _minor_amount(amount: int) -> int:
        """Validate and normalize an amount already expressed in minor units."""
        if isinstance(amount, bool):
            raise ValueError("amount must be an integer in minor currency units")
        try:
            normalized = int(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("amount must be an integer in minor currency units") from exc
        if normalized < 0:
            raise ValueError("amount cannot be negative")
        return normalized

    def _requests(self):
        """Return the requests module used by the legacy client.

        Keeping this indirection preserves the project's existing mocking seam
        while avoiding all credential/config mutation in that module.
        """
        import razorpay_client as rzp
        return rzp.requests

    def is_configured(self) -> bool:
        """Return whether both Razorpay API credentials are available."""
        return bool(
            self._setting("RAZORPAY_KEY_ID")
            and self._setting("RAZORPAY_KEY_SECRET")
        )

    def create_order(self, amount: int, currency: str, receipt: str,
                     notes: Optional[Dict[str, Any]] = None) -> PaymentResult:
        """Create a Razorpay order using an amount in minor units."""
        if not self.is_configured():
            return PaymentResult(success=False, error_message="Razorpay not configured")

        try:
            amount_minor = self._minor_amount(amount)
            currency_code = (currency or "").upper()
            if not currency_code:
                raise ValueError("currency is required")

            payload = {
                "amount": amount_minor,
                "currency": currency_code,
                "receipt": receipt,
                "notes": notes or {},
            }
            resp = self._requests().post(
                f"{self._api_base()}/orders",
                json=payload,
                auth=self._auth(),
                timeout=15,
            )
            resp.raise_for_status()
            result_data = resp.json()

            return PaymentResult(
                success=True,
                provider_order_id=result_data.get("id"),
                amount=amount_minor,
                currency=currency_code,
                status="created",
                raw_response=result_data,
            )
        except Exception as exc:
            self.logger.error("Failed to create Razorpay order: %s", exc)
            return PaymentResult(success=False, error_message=str(exc))

    def verify_payment_signature(self, order_id: str, payment_id: str,
                                 signature: str) -> bool:
        """Verify a Razorpay payment signature without changing global config."""
        if not self.is_configured():
            return False

        try:
            body = f"{order_id}|{payment_id}".encode("utf-8")
            expected = hmac.new(
                str(self._setting("RAZORPAY_KEY_SECRET")).encode("utf-8"),
                body,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, signature or "")
        except Exception as exc:
            self.logger.error("Failed to verify Razorpay signature: %s", exc)
            return False

    def capture_payment(self, payment_id: str,
                        amount: Optional[int] = None) -> PaymentResult:
        """Fetch a Razorpay payment and report its actual captured boolean."""
        if not self.is_configured():
            return PaymentResult(success=False, error_message="Razorpay not configured")

        try:
            resp = self._requests().get(
                f"{self._api_base()}/payments/{payment_id}",
                auth=self._auth(),
                timeout=15,
            )
            resp.raise_for_status()
            payment_data = resp.json()
            status = str(payment_data.get("status") or "").lower()
            # Razorpay returns this as a boolean. Do not infer success merely
            # from the HTTP response or from the presence of the payment ID.
            captured = bool(payment_data.get("captured", False))

            return PaymentResult(
                success=captured,
                provider_payment_id=payment_id,
                amount=payment_data.get("amount"),
                currency=payment_data.get("currency", "INR"),
                status=status,
                error_message=None if captured else "Payment is not captured",
                raw_response=payment_data,
            )
        except Exception as exc:
            self.logger.error("Failed to capture Razorpay payment: %s", exc)
            return PaymentResult(success=False, error_message=str(exc))

    def refund_payment(self, payment_id: str,
                       amount: Optional[int] = None,
                       idempotency_key: Optional[str] = None) -> PaymentResult:
        """Refund a Razorpay payment; optional amount is in minor units."""
        if not self.is_configured():
            return PaymentResult(success=False, error_message="Razorpay not configured")

        try:
            refund_amount = self._minor_amount(amount) if amount is not None else None
            payload = {"amount": refund_amount} if refund_amount else {}
            headers = {"Content-Type": "application/json"}
            if idempotency_key:
                headers["X-Refund-Idempotency"] = str(idempotency_key)
            resp = self._requests().post(
                f"{self._api_base()}/payments/{payment_id}/refund",
                json=payload,
                auth=self._auth(),
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            result_data = resp.json()

            return PaymentResult(
                success=True,
                provider_payment_id=payment_id,
                provider_refund_id=result_data.get("id"),
                amount=result_data.get("amount"),
                currency=result_data.get("currency", "INR"),
                status=str(result_data.get("status") or "pending").lower(),
                raw_response=result_data,
            )
        except Exception as exc:
            requests_mod = self._requests()
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            # Retry transient HTTP statuses and network failures. A normal 4xx
            # validation/authentication failure is terminal until configuration
            # or input is corrected and must not be retried indefinitely.
            retryable = (
                status_code in {409, 429, 500, 502, 503, 504}
                or isinstance(exc, (requests_mod.exceptions.Timeout, requests_mod.exceptions.ConnectionError))
            )
            self.logger.error("Failed to refund Razorpay payment: %s", exc)
            return PaymentResult(success=False, retryable=retryable, error_message=str(exc))

    def get_payment_status(self, payment_id: str) -> PaymentResult:
        """Get Razorpay payment status without changing global config."""
        if not self.is_configured():
            return PaymentResult(success=False, error_message="Razorpay not configured")

        try:
            resp = self._requests().get(
                f"{self._api_base()}/payments/{payment_id}",
                auth=self._auth(),
                timeout=15,
            )
            resp.raise_for_status()
            payment_data = resp.json()

            return PaymentResult(
                success=True,
                provider_payment_id=payment_id,
                amount=payment_data.get("amount"),
                currency=payment_data.get("currency", "INR"),
                status=payment_data.get("status"),
                raw_response=payment_data,
            )
        except Exception as exc:
            self.logger.error("Failed to get Razorpay payment status: %s", exc)
            return PaymentResult(success=False, error_message=str(exc))

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify a Razorpay webhook signature without global config mutation."""
        webhook_secret = self._setting("RAZORPAY_WEBHOOK_SECRET")
        if not webhook_secret:
            return False

        try:
            expected = hmac.new(
                str(webhook_secret).encode("utf-8"),
                payload,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, signature or "")
        except Exception as exc:
            self.logger.error("Failed to verify Razorpay webhook signature: %s", exc)
            return False


class TestPaymentGateway(PaymentGateway):
    """Synthetic gateway, available only when explicitly enabled by the environment.

    This gateway accepts every signature and simulates successful payments. It
    must never become available merely because a caller selected ``test`` in a
    production configuration.
    """

    @staticmethod
    def _enabled() -> bool:
        return os.environ.get("ALLOW_TEST_GATEWAY", "").strip().lower() in _TRUE_VALUES

    def _disabled_result(self) -> PaymentResult:
        return PaymentResult(
            success=False,
            error_message=(
                "Test payment gateway is disabled. Set ALLOW_TEST_GATEWAY=true "
                "only in a non-production environment."
            ),
        )

    def is_configured(self) -> bool:
        """Test gateway is configured only under an explicit environment flag."""
        return self._enabled()

    def create_order(self, amount: int, currency: str, receipt: str,
                     notes: Optional[Dict[str, Any]] = None) -> PaymentResult:
        """Create a synthetic order using the same minor-unit contract."""
        if not self.is_configured():
            return self._disabled_result()
        amount_minor = RazorpayGateway._minor_amount(amount)
        currency_code = (currency or "").upper()
        return PaymentResult(
            success=True,
            provider_order_id=f"test_order_{receipt}_{amount_minor}",
            provider_payment_id=f"test_payment_{receipt}_{amount_minor}",
            amount=amount_minor,
            currency=currency_code,
            status="created",
            raw_response={"test": True},
        )

    def verify_payment_signature(self, order_id: str, payment_id: str,
                                 signature: str) -> bool:
        """Synthetic signature verification, only while explicitly enabled."""
        return self.is_configured()

    def capture_payment(self, payment_id: str,
                        amount: Optional[int] = None) -> PaymentResult:
        """Capture a synthetic payment."""
        if not self.is_configured():
            return self._disabled_result()
        amount_minor = RazorpayGateway._minor_amount(amount or 0)
        return PaymentResult(
            success=True,
            provider_payment_id=payment_id,
            amount=amount_minor,
            currency="INR",
            status="captured",
            raw_response={"test": True, "captured": True},
        )

    def refund_payment(self, payment_id: str,
                       amount: Optional[int] = None,
                       idempotency_key: Optional[str] = None) -> PaymentResult:
        """Refund a synthetic payment."""
        if not self.is_configured():
            return self._disabled_result()
        amount_minor = RazorpayGateway._minor_amount(amount or 0)
        return PaymentResult(
            success=True,
            provider_payment_id=payment_id,
            provider_refund_id=f"test_refund_{idempotency_key or payment_id}",
            amount=amount_minor,
            currency="INR",
            status="processed",
            raw_response={"test": True},
        )

    def get_payment_status(self, payment_id: str) -> PaymentResult:
        """Get synthetic payment status."""
        if not self.is_configured():
            return self._disabled_result()
        return PaymentResult(
            success=True,
            provider_payment_id=payment_id,
            amount=0,
            currency="INR",
            status="captured",
            raw_response={"test": True, "captured": True},
        )

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Synthetic webhook verification, only while explicitly enabled."""
        return self.is_configured()


def get_payment_gateway(gateway_type: str = "razorpay",
                        config: Optional[Dict[str, Any]] = None) -> PaymentGateway:
    """Return a configured payment gateway instance."""
    config = config or {}
    gateway_name = gateway_type.lower()
    if gateway_name == "razorpay":
        return RazorpayGateway(config)
    if gateway_name == "test":
        return TestPaymentGateway(config)
    raise ValueError(f"Unsupported gateway type: {gateway_type}")
