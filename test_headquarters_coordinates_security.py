import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


class HeadquartersCoordinatesSecurityTest(unittest.TestCase):
    def load_app(self, latitude="", longitude=""):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / "app.py"
        shutil.copyfile(ROOT / "app.py", source)
        spec = importlib.util.spec_from_file_location(
            f"headquarters_coordinates_security_{len(self._cleanups)}",
            source,
        )
        module = importlib.util.module_from_spec(spec)
        with patch.dict(
            os.environ,
            {
                "ADMIN_EMAIL": "headquarters-admin@example.test",
                "ADMIN_PASSWORD": "headquarters-test-password",
                "APP_VERSION": "0.0.0",
                "REQUIRE_DATA_DIRECTORY_IDENTITY": "0",
                "HEADQUARTERS_LATITUDE": latitude,
                "HEADQUARTERS_LONGITUDE": longitude,
            },
            clear=False,
        ):
            spec.loader.exec_module(module)
        return module

    def test_invalid_coordinate_pairs_are_unavailable(self):
        cases = (
            ("", ""),
            ("29.123", ""),
            ("", "-95.456"),
            ("not-a-number", "-95.456"),
            ("29.123", "not-a-number"),
            ("90.001", "-95.456"),
            ("29.123", "-180.001"),
        )
        for latitude, longitude in cases:
            with self.subTest(latitude=latitude, longitude=longitude):
                module = self.load_app(latitude, longitude)
                self.assertIsNone(module.headquarters_coordinates())

    def test_valid_coordinate_pair_is_preserved_as_numbers(self):
        module = self.load_app("29.123", "-95.456")
        self.assertEqual(
            module.headquarters_coordinates(),
            {"latitude": 29.123, "longitude": -95.456},
        )


if __name__ == "__main__":
    unittest.main()
