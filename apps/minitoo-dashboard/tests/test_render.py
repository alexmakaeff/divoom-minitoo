import unittest

from minitoo_dashboard import i18n, render
from minitoo_dashboard.sources.claude import ClaudeView, Window

NOW = 1_790_600_000.0


def model(lang="en", **overrides):
    m = render.demo_model(NOW, lang)
    for key, value in overrides.items():
        setattr(m, key, value)
    return m


def lit(img, box):
    x0, y0, x1, y1 = box
    return any(img.getpixel((x, y)) != render.BLACK for x in range(x0, x1) for y in range(y0, y1))


class GlyphTest(unittest.TestCase):
    def test_font_covers_needed_characters(self):
        for ch in "Жё°%+-:./":
            self.assertTrue(render.has_glyph(ch), ch)

    def test_emoji_not_covered(self):
        self.assertFalse(render.has_glyph("🎉"))


class ScreenTest(unittest.TestCase):
    def test_single_frame(self):
        img = render.render_screen(model())
        self.assertEqual((img.size, img.mode), ((160, 128), "RGB"))

    def test_badge_colour(self):
        self.assertEqual(render.render_screen(model()).getpixel((151, 119)), render.ORANGE)
        self.assertEqual(render.render_screen(model(status="chilling")).getpixel((151, 119)), render.GREY)

    def test_rows_are_drawn(self):
        img = render.render_screen(model())
        self.assertTrue(lit(img, (0, 0, 160, 40)))      # weather
        self.assertTrue(lit(img, (0, 44, 160, 82)))     # event
        self.assertTrue(lit(img, (0, 86, 140, 128)))    # claude

    def test_missing_data_still_renders_every_row(self):
        empty = Window(None, None, False)
        img = render.render_screen(model(weather=None, event=None, claude=ClaudeView(empty, empty, None, False)))
        self.assertTrue(lit(img, (0, 44, 160, 82)))     # "No events left"
        self.assertTrue(lit(img, (0, 86, 140, 128)))    # "no data yet"

    def test_ongoing_event(self):
        m = model()
        m.event.start = NOW - 600
        self.assertEqual(render.render_screen(m).size, (160, 128))

    def test_emoji_and_long_title(self):
        m = model(lang="ru")
        m.event.title = "Встреча 🎉 с очень-очень длинным названием, которое не помещается в экран"
        self.assertEqual(render.render_screen(m).size, (160, 128))

    def test_full_limit_stays_on_screen(self):
        full = ClaudeView(Window(100.0, 600, False), Window(100.0, 3600, False), None, True)
        img = render.render_screen(model(claude=full))
        self.assertFalse(lit(img, (156, 86, 160, 112)))

    def test_stale_and_reset_states_render(self):
        stale = ClaudeView(Window(None, None, True), Window(55.0, 3600, False), NOW - 3600, True)
        self.assertEqual(render.render_screen(model(claude=stale)).size, (160, 128))

    def test_wrap_limits_lines(self):
        lines = render.wrap("one two three four five six seven eight nine ten eleven twelve", render.font(8), 148, 3)
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("..."))
        for line in lines:
            self.assertLessEqual(render.font(8).getlength(line), 148)

    def test_deterministic(self):
        self.assertEqual(render.render_screen(model()).tobytes(), render.render_screen(model()).tobytes())

    def test_alert_page(self):
        page = render.render_alert("en")
        self.assertEqual(page.size, (160, 128))
        self.assertEqual(page.getpixel((1, 1)), render.RED)


class I18nTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(i18n.duration(7800, "en"), "2h10m")
        self.assertEqual(i18n.duration(45 * 60, "ru"), "45м")
        self.assertEqual(i18n.duration(3 * 86400 + 3600, "en"), "3d1h")
        self.assertEqual(i18n.duration(10, "en"), "1m")

    def test_format(self):
        self.assertEqual(i18n.t("ru", "reset_in", d="2ч10м"), "сброс 2ч10м")


if __name__ == "__main__":
    unittest.main()
