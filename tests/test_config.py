import os
import unittest
from unittest.mock import patch

from maestro_gateway.config import GatewayConfig

class GatewayConfigTests(unittest.TestCase):
    def test_default_host_is_loopback(self):
        with patch.dict(os.environ, {}, clear=True):
            config = GatewayConfig.from_env()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8787)

    def test_non_loopback_host_is_rejected(self):
        with patch.dict(
            os.environ,
            {"MAESTRO_GATEWAY_HOST": "0.0.0.0"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                GatewayConfig.from_env()

    def test_wildcard_cors_is_rejected(self):
        with patch.dict(
            os.environ,
            {"MAESTRO_GATEWAY_ALLOWED_ORIGINS": "*"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                GatewayConfig.from_env()

if __name__ == "__main__":
    unittest.main()
