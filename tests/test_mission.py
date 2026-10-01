import unittest

from maestro_gateway.mission import (
    _requested_resource_id,
    validate_input,
)


class MissionInputTests(unittest.TestCase):
    def test_resource_id_normalization(self):
        self.assertEqual(
            _requested_resource_id("qwen3:8b"),
            "ollama::qwen3:8b",
        )
        self.assertEqual(
            _requested_resource_id("ollama::gemma4:e4b"),
            "ollama::gemma4:e4b",
        )

    def test_empty_prompt_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_input("   ", 512)

    def test_token_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            validate_input("hello", 4096)

        prompt, tokens = validate_input("hello", 512)
        self.assertEqual(prompt, "hello")
        self.assertEqual(tokens, 512)


if __name__ == "__main__":
    unittest.main()
