import unittest

import config


class ConfigTest(unittest.TestCase):
    def test_v21_traceback_setting(self):
        value = getattr(config, "BACKGROUND_TRACEBACK_CHARS", None)
        self.assertIsInstance(value, int)
        self.assertGreaterEqual(value, 0)

    def test_v14_settings_still_present(self):
        for name in ("GITHUB_REPO", "PHONE_TOKEN", "PHONE_ALLOW_CORS", "BACKGROUND_MAX_RETRIES"):
            self.assertTrue(hasattr(config, name), name)


if __name__ == "__main__":
    unittest.main()
