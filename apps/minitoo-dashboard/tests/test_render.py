import unittest

from minitoo_dashboard import i18n, render
from minitoo_dashboard.limit_alerts import Crossing
from minitoo_dashboard.sources.calendar import Item
from minitoo_dashboard.sources.claude import LimitsView, Window

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
        img = render.render_screen(model(weather=None, event=None, claude=LimitsView(empty, empty, None, False)))
        self.assertTrue(lit(img, (0, 44, 160, 82)))     # "No events left"
        self.assertTrue(lit(img, (0, 86, 140, 128)))    # "no data yet"

    def test_ongoing_event(self):
        m = model()
        m.event.start = NOW - 600
        self.assertEqual(render.render_screen(m).size, (160, 128))

    def has_color(self, img, box, color):
        x0, y0, x1, y1 = box
        return any(img.getpixel((x, y)) == color for x in range(x0, x1) for y in range(y0, y1))

    def test_reminder_has_checkbox(self):
        corners = [(4, 68), (11, 68), (4, 75), (11, 75)]
        img = render.render_screen(model(event=Item("reminder", "", NOW + 3600, NOW + 3600)))
        self.assertEqual([img.getpixel(p) for p in corners], [render.WHITE] * 4)
        event_img = render.render_screen(model(event=Item("event", "", NOW + 3600, NOW + 7200)))
        self.assertNotEqual([event_img.getpixel(p) for p in corners], [render.WHITE] * 4)

    def test_overdue_label_is_yellow_not_red(self):
        img = render.render_screen(model(lang="ru", event=Item("overdue", "Счёт", NOW - 7200, NOW - 7200)))
        self.assertTrue(self.has_color(img, (4, 46, 100, 58), render.YELLOW))
        self.assertFalse(self.has_color(img, (0, 44, 160, 82), render.RED))

    def test_today_reminder_has_its_own_label(self):
        today = render.render_screen(model(event=Item("today", "Call mom", NOW - 3600, NOW - 3600)))
        as_event = render.render_screen(model(event=Item("event", "Call mom", NOW - 3600, NOW - 3600)))
        crop = (4, 44, 150, 66)
        self.assertTrue(lit(today, crop))
        self.assertNotEqual(today.crop(crop).tobytes(), as_event.crop(crop).tobytes())

    def test_counter_shown_only_when_more(self):
        counter = (136, 46, 156, 60)
        with_more = model()
        with_more.event.more = 3
        self.assertTrue(lit(render.render_screen(with_more), counter))
        self.assertFalse(lit(render.render_screen(model()), counter))

    def test_emoji_and_long_title(self):
        m = model(lang="ru")
        m.event.title = "Встреча 🎉 с очень-очень длинным названием, которое не помещается в экран"
        self.assertEqual(render.render_screen(m).size, (160, 128))

    def test_full_limit_stays_on_screen(self):
        full = LimitsView(Window(100.0, 600, False), Window(100.0, 3600, False), None, True)
        img = render.render_screen(model(claude=full))
        self.assertFalse(lit(img, (156, 86, 160, 112)))

    def test_stale_and_reset_states_render(self):
        stale = LimitsView(Window(None, None, True), Window(55.0, 3600, False), NOW - 3600, True)
        self.assertEqual(render.render_screen(model(claude=stale)).size, (160, 128))

    def table(self, **kw):
        view = LimitsView(Window(58.0, 480, False), Window(21.0, 86400, False), None, True)
        return model(**dict({"codex": view, "codex_working": False}, **kw))

    def test_codex_off_keeps_badge(self):
        self.assertIsNone(model().codex)
        self.assertEqual(render.render_screen(model()).getpixel((151, 119)), render.ORANGE)

    def test_table_squares(self):
        img = render.render_screen(self.table())
        self.assertEqual(img.getpixel((79, 89)), render.ORANGE)   # Claude working
        self.assertEqual(img.getpixel((139, 89)), render.GREY)    # Codex idle
        img = render.render_screen(self.table(status="chilling", codex_working=True))
        self.assertEqual(img.getpixel((79, 89)), render.GREY)
        self.assertEqual(img.getpixel((139, 89)), render.TEAL)

    def test_table_bars_in_service_colours(self):
        img = render.render_screen(self.table())
        self.assertTrue(self.has_color(img, (24, 97, 54, 104), render.ORANGE))
        self.assertTrue(self.has_color(img, (92, 97, 122, 104), render.TEAL))
        self.assertNotEqual(img.getpixel((151, 119)), render.ORANGE)  # no bottom-right badge

    def test_table_full_limits_stay_on_screen(self):
        full = LimitsView(Window(100.0, 600, False), Window(100.0, 3600, False), None, True)
        img = render.render_screen(self.table(claude=full, codex=full))
        self.assertFalse(lit(img, (156, 86, 160, 128)))
        self.assertFalse(lit(img, (89, 97, 92, 115)))  # Claude "100%" stops before the Codex column

    def test_table_without_codex_data(self):
        empty = Window(None, None, False)
        img = render.render_screen(self.table(codex=LimitsView(empty, empty, None, False)))
        self.assertFalse(self.has_color(img, (92, 97, 122, 115), render.TEAL))
        self.assertTrue(lit(img, (124, 97, 156, 104)))  # "--"

    def test_codex_shows_reset_timer_even_when_stale(self):
        fresh = LimitsView(Window(58.0, 480, False), Window(21.0, 86400, False), None, True)
        stale = LimitsView(Window(58.0, 480, False), Window(21.0, 86400, False), NOW - 3600, True)
        for lang in ("en", "ru"):
            self.assertEqual(render.render_screen(model(lang, codex=stale)).tobytes(),
                             render.render_screen(model(lang, codex=fresh)).tobytes())

    def test_claude_column_still_shows_as_of(self):
        fresh = LimitsView(Window(23.0, 480, False), Window(41.0, 86400, False), None, True)
        stale = LimitsView(Window(23.0, 480, False), Window(41.0, 86400, False), NOW - 3600, True)
        self.assertNotEqual(render.render_screen(self.table(claude=stale)).tobytes(),
                            render.render_screen(self.table(claude=fresh)).tobytes())

    def test_table_stale_footer_fits(self):
        stale = LimitsView(Window(None, None, True), Window(55.0, 3600, False), NOW - 3600, True)
        for lang in ("en", "ru"):
            img = render.render_screen(model(lang, codex=stale, claude=stale))
            self.assertTrue(lit(img, (92, 119, 156, 127)))
            self.assertFalse(lit(img, (156, 119, 160, 128)))

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


    def test_limit_alert_page_in_service_colour(self):
        claude = render.render_limit_alert(Crossing("claude", "five_hour", 92.4, NOW + 4800), "en", NOW)
        codex = render.render_limit_alert(Crossing("codex", "seven_day", 90.0, NOW + 86400), "ru", NOW)
        self.assertEqual(claude.size, (160, 128))
        self.assertEqual(claude.getpixel((1, 1)), render.ORANGE)
        self.assertEqual(codex.getpixel((1, 1)), render.TEAL)

    def test_limit_alert_text_stays_inside_border(self):
        for lang in ("en", "ru"):
            for window in ("five_hour", "seven_day"):
                page = render.render_limit_alert(Crossing("codex", window, 100.0, NOW + 6 * 86400), lang, NOW)
                inner = [page.getpixel((x, y)) for y in range(4, 124) for x in (4, 5, 154, 155)]
                self.assertEqual(set(inner), {render.BLACK}, (lang, window))


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
