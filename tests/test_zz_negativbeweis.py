# Negativbeweis Auto-Freigabe (2026-10-24): rot im ersten Versuch, grün im Neustart.
# Simuliert einen wackligen Test. PR wird nie gemergt.
import os
import unittest


class Negativbeweis(unittest.TestCase):
    def test_erster_versuch_rot(self):
        self.assertNotEqual(os.environ.get("GITHUB_RUN_ATTEMPT", "1"), "1")


if __name__ == "__main__":
    unittest.main()
