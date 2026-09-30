import unittest

from minitoo_dashboard.sources import weather

FORECAST = {
    "current": {"time": "2026-09-29T17:45", "temperature_2m": 11.6, "weather_code": 3},
    "hourly": {"time": ["2026-09-29T16:00", "2026-09-29T17:00", "2026-09-29T18:00", "2026-09-29T19:00"],
               "precipitation_probability": [90, 10, 60, 80], "weather_code": [61, 3, 71, 61]},
    "daily": {"temperature_2m_max": [15.2], "temperature_2m_min": [7.6]},
}


class FakeGet:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, url, params, timeout=10):
        self.calls.append((url, params))
        return self.response


class FetchWeatherTest(unittest.TestCase):
    def test_parses_forecast(self):
        get = FakeGet(FORECAST)
        rec = weather.fetch_weather(55.75, 37.61, "celsius", 1000.0, get=get)
        self.assertEqual(rec, {"fetched_at": 1000.0, "lat": 55.75, "lon": 37.61, "temp": 12, "code": 3,
                               "tmax": 15, "tmin": 8, "precip_from": "18:00", "precip_kind": "snow"})
        url, params = get.calls[0]
        self.assertEqual(url, weather.FORECAST_URL)
        self.assertEqual(params["temperature_unit"], "celsius")
        self.assertEqual(params["timezone"], "auto")

    def test_precip_now(self):
        data = {**FORECAST, "hourly": {"time": ["2026-09-29T17:00"], "precipitation_probability": [70],
                                       "weather_code": [61]}}
        rec = weather.fetch_weather(0, 0, "celsius", 0, get=FakeGet(data))
        self.assertEqual((rec["precip_from"], rec["precip_kind"]), ("now", "rain"))

    def test_no_precip(self):
        data = {**FORECAST, "hourly": {"time": ["2026-09-29T18:00"], "precipitation_probability": [None],
                                       "weather_code": [3]}}
        rec = weather.fetch_weather(0, 0, "celsius", 0, get=FakeGet(data))
        self.assertIsNone(rec["precip_from"])


class ConditionTest(unittest.TestCase):
    def test_codes(self):
        cases = {0: "clear", 2: "partly", 3: "cloudy", 45: "fog", 63: "rain", 81: "rain",
                 75: "snow", 86: "snow", 95: "storm", 1234: "cloudy"}
        for code, key in cases.items():
            self.assertEqual(weather.condition_key(code), key, code)


class ViewTest(unittest.TestCase):
    def rec(self, fetched_at):
        return weather.fetch_weather(1, 2, "celsius", fetched_at, get=FakeGet(FORECAST))

    def test_none(self):
        self.assertIsNone(weather.weather_view(None, 0))

    def test_fresh(self):
        view = weather.weather_view(self.rec(1000), 1000 + 600)
        self.assertEqual(view, weather.WeatherView(12, 15, 8, "cloudy", "18:00", "snow", None))

    def test_aged(self):
        self.assertEqual(weather.weather_view(self.rec(0), 7200).age_s, 7200)

    def test_too_old(self):
        self.assertIsNone(weather.weather_view(self.rec(0), 7 * 3600))


class GeocodeTest(unittest.TestCase):
    def test_labels(self):
        get = FakeGet({"results": [
            {"name": "Paris", "admin1": "Île-de-France", "country": "France", "latitude": 48.85, "longitude": 2.35},
            {"name": "Paris", "admin1": "Texas", "country": "United States", "latitude": 33.66, "longitude": -95.55},
            {"name": "Moscow", "admin1": "Moscow", "country": "Russia", "latitude": 55.75, "longitude": 37.61},
            {"name": "Broken"},
        ]})
        places = weather.geocode("Paris", "en", get=get)
        self.assertEqual([p.label() for p in places],
                         ["Paris, Île-de-France, France", "Paris, Texas, United States", "Moscow, Russia"])
        self.assertEqual(get.calls[0][1]["language"], "en")

    def test_no_results(self):
        self.assertEqual(weather.geocode("Nowhere", "en", get=FakeGet({})), [])


if __name__ == "__main__":
    unittest.main()
