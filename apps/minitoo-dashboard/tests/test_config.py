import tempfile
import unittest
from pathlib import Path

from minitoo_dashboard import config


class ParseConfigTest(unittest.TestCase):
    def test_full_config(self):
        cfg = config.parse_config(
            "DEVICE_MAC=aa:bb:cc:dd:ee:01\n"
            'CITY_NAME="Moscow, Russia"\n'
            "CITY_LAT=55.7558\nCITY_LON=37.6173\n"
            "TEMP_UNIT=fahrenheit\nLANG=ru\nCALENDARS=Work, Home\n"
            "PAGE_SECONDS=10\nSEND_DELAY_MS=5\n"  # PAGE_SECONDS: obsolete key, ignored
        )
        self.assertEqual(cfg.device_mac, "AA:BB:CC:DD:EE:01")
        self.assertEqual(cfg.city_name, "Moscow, Russia")
        self.assertAlmostEqual(cfg.city_lat, 55.7558)
        self.assertAlmostEqual(cfg.city_lon, 37.6173)
        self.assertEqual(cfg.temp_unit, "fahrenheit")
        self.assertEqual(cfg.lang, "ru")
        self.assertFalse(hasattr(cfg, "page_seconds"))
        self.assertEqual(cfg.send_delay_ms, 5)
        self.assertTrue(cfg.has_city)
        self.assertEqual(cfg.calendar_list(), ["Work", "Home"])

    def test_defaults_and_invalid_values(self):
        cfg = config.parse_config("# comment\nLANG=de\nTEMP_UNIT=kelvin\nPAGE_SECONDS=abc\nCITY_LAT=north\nJUNK\n")
        self.assertEqual(cfg.lang, "en")
        self.assertEqual(cfg.temp_unit, "celsius")
        self.assertIsNone(cfg.city_lat)
        self.assertFalse(cfg.has_city)
        self.assertIsNone(cfg.calendar_list())

    def test_send_delay_clamped(self):
        self.assertEqual(config.parse_config("SEND_DELAY_MS=900").send_delay_ms, 200)
        self.assertEqual(config.parse_config("SEND_DELAY_MS=-5").send_delay_ms, 0)

    def test_inline_comment(self):
        self.assertEqual(config.parse_config("TEMP_UNIT=fahrenheit  # US").temp_unit, "fahrenheit")

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            cfg = config.Config(device_mac="AA:BB:CC:DD:EE:FF", city_name="Kazan, Tatarstan, Russia",
                                city_lat=55.79, city_lon=49.12, lang="ru")
            config.save_config(cfg, path)
            self.assertEqual(config.load_config(path), cfg)

    def test_load_missing_file_returns_defaults(self):
        self.assertEqual(config.load_config(Path("/nonexistent/config")), config.Config())


class ClauddyAlertTest(unittest.TestCase):
    def test_reads_clock_and_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            path.write_text("# c\nCLAUDDY_DEVICE_ID=123456789\nCLAUDDY_CLOCK_ALERTING=988\n")
            self.assertEqual(config.clauddy_alert(path), (988, 123456789))

    def test_missing_returns_none(self):
        self.assertIsNone(config.clauddy_alert(Path("/nonexistent/clauddy")))


if __name__ == "__main__":
    unittest.main()
