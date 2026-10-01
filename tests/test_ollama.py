import unittest
from unittest.mock import patch

from maestro_gateway.ollama import discover_ollama

class OllamaTests(unittest.TestCase):
    @patch(
        "maestro_gateway.ollama._get_json",
        return_value={
            "models": [
                {"name": "qwen3:8b"},
                {"name": "gemma4:e4b"},
            ]
        },
    )
    def test_model_discovery(self, mocked):
        result = discover_ollama()
        self.assertEqual(result["status"], "online")
        self.assertEqual(
            result["models"],
            ["gemma4:e4b", "qwen3:8b"],
        )
        mocked.assert_called_once()

if __name__ == "__main__":
    unittest.main()
