import unittest

from minitoo_dashboard import i18n, render
from minitoo_dashboard.sources.claude import ClaudeView, Window

NOW = 1_790_600_000.0


def model(**overrides):
    m = render.demo_model(NOW, "en")
    for key, value in overrides.items():
        setattr(m, key, value)
    return m


class GlyphTest(unittest.TestCase):
    def test_font_covers_needed_characters(self):
        for ch in "Жё°%+-:.":
            self.assertTrue(render.has_glyph(ch), ch)

    def test_emoji_not_covered(self):
        self.assertFalse(render.has_glyph("🎉"))


class PagesTest(unittest.TestCase):
    def test_demo_has_three_pages(self):
        pages = render.render_pages(model())
        self.assertEqual(len(pages), 3)
        for page in pages:
            self.assertEqual((page.size, page.mode), ((160, 128), "RGB"))

    def test_pages_skipped(self):
        self.assertEqual(len(render.render_pages(model(event=None))), 2)
        self.assertEqual(len(render.render_pages(model(event=None, weather=None))), 1)

    def test_claude_page_without_data(self):
        empty = Window(None, None, False)
        pages = render.render_pages(model(event=None, weather=None, claude=ClaudeView(empty, empty, None, False)))
        self.assertEqual(len(pages), 1)

    def test_badge_colour(self):
        self.assertEqual(render.render_pages(model())[0].getpixel((151, 119)), render.ORANGE)
        self.assertEqual(render.render_pages(model(status="chilling"))[0].getpixel((151, 119)), render.GREY)

    def test_emoji_and_long_title(self):
        m = model()
        m.event.title = "Встреча 🎉 с очень-очень длинным названием, которое не помещается в экран"
        pages = render.render_pages(m)
        self.assertEqual(len(pages), 3)

    def test_wrap_limits_lines(self):
        lines = render.wrap("one two three four five six seven eight nine ten eleven twelve", render.font(8), 148, 3)
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("..."))
        for line in lines:
            self.assertLessEqual(render.font(8).getlength(line), 148)

    def test_russian(self):
        self.assertEqual(len(render.render_pages(render.demo_model(NOW, "ru"))), 3)

    def test_deterministic(self):
        a = [p.tobytes() for p in render.render_pages(model())]
        b = [p.tobytes() for p in render.render_pages(model())]
        self.assertEqual(a, b)

    def test_big_percent_fits_before_label(self):
        for text in ("23%", "100%", "--"):
            self.assertLessEqual(render.fit_font(text, 78).getlength(text), 78, text)
        self.assertEqual(render.fit_font("23%", 78).size, 24)

    def test_full_limit_does_not_touch_label_column(self):
        full = ClaudeView(Window(100.0, 600, False), Window(100.0, 3600, False), None, True)
        page = render.render_pages(model(event=None, weather=None, claude=full))[0]
        for x in range(80, 86):
            for y in range(20, 45):
                self.assertEqual(page.getpixel((x, y)), render.BLACK, (x, y))

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
        self.assertEqual(i18n.t("ru", "in_min", m=5), "через 5 мин")


if __name__ == "__main__":
    unittest.main()
