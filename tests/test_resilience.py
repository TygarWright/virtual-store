from __future__ import annotations

import unittest
from utils.resilience import CircuitBreaker, CircuitOpenError, retry_call


class ResilienceTests(unittest.TestCase):
    def test_retry_call_retries_transient_error(self):
        state = {"n": 0}
        def fn():
            state["n"] += 1
            if state["n"] < 3:
                raise TimeoutError("temporary")
            return "ok"
        self.assertEqual(retry_call(fn, retries=2, retry_if=lambda exc: isinstance(exc, TimeoutError), base_delay=0, jitter=0), "ok")
        self.assertEqual(state["n"], 3)

    def test_circuit_opens_and_recovers(self):
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_seconds=0.01)
        breaker.failure(); breaker.failure()
        with self.assertRaises(CircuitOpenError):
            breaker.before_call()


if __name__ == "__main__":
    unittest.main()
