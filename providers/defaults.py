from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Iterable

import config
from .contracts import AuthProvider, NotificationProvider, NotificationResult, SearchProvider, StorageProvider


class VirtualStoreEmailProvider(NotificationProvider):
    """Adapter around the existing multi-provider email helper."""

    def send_email(self, to: str, subject: str, body: str) -> NotificationResult:
        from helpers import send_email
        try:
            result = send_email(to, subject, body)
            return NotificationResult(success=bool(result), provider="virtual-store-email")
        except Exception as exc:
            return NotificationResult(success=False, provider="virtual-store-email", retryable=True, error=str(exc))


class FirebaseAuthProvider(AuthProvider):
    def enabled(self) -> bool:
        from helpers import firebase_auth_enabled
        return bool(firebase_auth_enabled())

    def verify_identity_token(self, token: str) -> Optional[Dict[str, Any]]:
        from helpers import verify_firebase_id_token
        return verify_firebase_id_token(token)


class LocalStorageProvider(StorageProvider):
    def save(self, file_storage: Any) -> str:
        from helpers import save_product_file
        return save_product_file(file_storage)

    def resolve(self, stored_name: str) -> str:
        from helpers import product_file_path
        return product_file_path(stored_name)


class SimpleSearchProvider(SearchProvider):
    """Deterministic in-process search used until metrics justify a search service."""

    def search(self, products: Sequence[Dict[str, Any]], query: str) -> Iterable[Dict[str, Any]]:
        tokens = [t.casefold() for t in (query or "").split() if len(t) >= 2]
        if not tokens:
            return list(products)
        scored = []
        for product in products:
            name = str(product.get("name") or "").casefold()
            desc = str(product.get("short_description") or "").casefold()
            category = str(product.get("category") or "").casefold()
            score = 0
            matched = False
            for token in tokens:
                if token in name:
                    score += 10
                    matched = True
                elif token in category:
                    score += 5
                    matched = True
                elif token in desc:
                    score += 3
                    matched = True
            if matched:
                scored.append((score, str(product.get("name") or ""), product))
        scored.sort(key=lambda row: (-row[0], row[1].casefold()))
        return [row[2] for row in scored]
