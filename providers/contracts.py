from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence


@dataclass(frozen=True)
class NotificationResult:
    success: bool
    provider: str = ""
    message_id: Optional[str] = None
    retryable: bool = False
    error: Optional[str] = None


class NotificationProvider(ABC):
    @abstractmethod
    def send_email(self, to: str, subject: str, body: str) -> NotificationResult:
        raise NotImplementedError


class AuthProvider(ABC):
    @abstractmethod
    def enabled(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def verify_identity_token(self, token: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError


class StorageProvider(ABC):
    @abstractmethod
    def save(self, file_storage: Any) -> str:
        raise NotImplementedError

    @abstractmethod
    def resolve(self, stored_name: str) -> str:
        raise NotImplementedError


class SearchProvider(ABC):
    @abstractmethod
    def search(self, products: Sequence[Dict[str, Any]], query: str) -> Iterable[Dict[str, Any]]:
        raise NotImplementedError
