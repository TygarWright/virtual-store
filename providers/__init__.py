"""Provider contracts and default adapters for Virtual Store.

External systems are deliberately kept behind small interfaces so the core
commerce workflows do not depend directly on a specific vendor.
"""
from .contracts import (
    AuthProvider,
    NotificationProvider,
    SearchProvider,
    StorageProvider,
)
from .defaults import (
    FirebaseAuthProvider,
    LocalStorageProvider,
    SimpleSearchProvider,
    VirtualStoreEmailProvider,
)

__all__ = [
    "AuthProvider",
    "NotificationProvider",
    "SearchProvider",
    "StorageProvider",
    "FirebaseAuthProvider",
    "LocalStorageProvider",
    "SimpleSearchProvider",
    "VirtualStoreEmailProvider",
]
