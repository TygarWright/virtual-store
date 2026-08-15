"""Small, dependency-free resilience primitives for external providers.

Inspired by battle-tested retry/circuit-breaker patterns, but intentionally
small enough for Virtual Store. Safe retries require the caller to explicitly
opt in; financial POSTs must provide an idempotency key before retrying.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, TypeVar

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    """Raised when a provider circuit is open and calls should fail fast."""


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float = 0.0
    half_open: bool = False


class CircuitBreaker:
    def __init__(self, name: str, *, failure_threshold: int = 5, recovery_seconds: float = 30.0):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = max(1.0, float(recovery_seconds))
        self._state = CircuitState()
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._state.opened_at <= 0:
                return
            elapsed = time.monotonic() - self._state.opened_at
            if elapsed >= self.recovery_seconds:
                if not self._state.half_open:
                    self._state.half_open = True
                    return
            raise CircuitOpenError(f"provider circuit open: {self.name}")

    def success(self) -> None:
        with self._lock:
            self._state = CircuitState()

    def failure(self) -> None:
        with self._lock:
            self._state.failures += 1
            self._state.half_open = False
            if self._state.failures >= self.failure_threshold:
                self._state.opened_at = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "failures": self._state.failures,
                "open": bool(self._state.opened_at),
                "half_open": self._state.half_open,
            }


def retry_call(
    fn: Callable[[], T],
    *,
    retries: int = 2,
    retry_if: Optional[Callable[[BaseException], bool]] = None,
    base_delay: float = 0.25,
    max_delay: float = 4.0,
    jitter: float = 0.15,
) -> T:
    """Retry an explicitly safe operation with bounded exponential backoff."""
    retries = max(0, int(retries))
    last: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except BaseException as exc:
            last = exc
            if attempt >= retries or (retry_if and not retry_if(exc)):
                raise
            delay = min(max_delay, base_delay * (2 ** attempt))
            if jitter:
                delay += random.uniform(0, delay * jitter)
            time.sleep(delay)
    assert last is not None
    raise last


class ResilientProviderCall:
    """Retry + circuit-break a provider operation explicitly approved by caller."""

    def __init__(self, breaker: CircuitBreaker, *, retries: int = 2, retry_if=None):
        self.breaker = breaker
        self.retries = retries
        self.retry_if = retry_if

    def __call__(self, fn: Callable[[], T]) -> T:
        self.breaker.before_call()
        try:
            result = retry_call(fn, retries=self.retries, retry_if=self.retry_if)
        except BaseException:
            self.breaker.failure()
            raise
        self.breaker.success()
        return result
