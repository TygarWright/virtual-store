import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestTestingModeStaticContract(unittest.TestCase):
    def test_config_has_safe_store_simulator_and_razorpay_environment(self):
        text = (ROOT / "config.py").read_text(encoding="utf-8")
        self.assertIn("ALLOW_STORE_TEST_MODE", text)
        self.assertIn("TEST_MODE_SEND_EMAILS", text)
        self.assertIn('rzp_test_', text)
        self.assertIn('razorpay_environment', text)

    def test_store_simulator_is_environment_gated(self):
        text = (ROOT / "helpers.py").read_text(encoding="utf-8")
        self.assertIn("ALLOW_STORE_TEST_MODE", text)
        self.assertIn("is_testing_checkout", text)
        self.assertIn("TEST_MODE_SEND_EMAILS", text)

    def test_admin_cannot_enable_locked_test_mode(self):
        text = (ROOT / "blueprints/admin.py").read_text(encoding="utf-8")
        self.assertIn("Testing mode is locked by the deployment", text)
        self.assertIn("config.ALLOW_STORE_TEST_MODE", text)

    def test_admin_explains_sandbox_vs_simulator(self):
        text = (ROOT / "templates/admin/settings.html").read_text(encoding="utf-8")
        self.assertIn("Razorpay Sandbox", text)
        self.assertIn("Store Test Mode", text)
        self.assertIn("rzp_test_", text)


if __name__ == "__main__":
    unittest.main()
