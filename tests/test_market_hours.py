import datetime
import importlib.util
import pathlib
import unittest

import pytz

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "webui" / "utils" / "market_hours.py"
SPEC = importlib.util.spec_from_file_location("market_hours_under_test", MODULE_PATH)
market_hours = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(market_hours)

get_next_market_datetime = market_hours.get_next_market_datetime
is_market_open = market_hours.is_market_open
validate_market_hours = market_hours.validate_market_hours
format_market_hours_info = market_hours.format_market_hours_info

EASTERN = pytz.timezone("US/Eastern")


def _eastern(*args):
    return EASTERN.localize(datetime.datetime(*args))


class MarketHoursTests(unittest.TestCase):
    def test_naive_datetime_is_treated_as_eastern_wall_clock(self):
        is_open, reason = is_market_open(datetime.datetime(2025, 1, 6, 10, 0))
        self.assertTrue(is_open, reason)

    def test_aware_utc_datetime_is_converted_to_eastern(self):
        is_open, reason = is_market_open(datetime.datetime(2025, 1, 6, 15, 0, tzinfo=pytz.utc))
        self.assertTrue(is_open, reason)

    def test_2026_nyse_holiday_is_closed(self):
        eastern = pytz.timezone("US/Eastern")
        is_open, reason = is_market_open(eastern.localize(datetime.datetime(2026, 7, 3, 10, 0)))
        self.assertFalse(is_open)
        self.assertIn("holiday", reason.lower())

    def test_next_market_datetime_uses_eastern_schedule_from_aware_input(self):
        start = datetime.datetime(2025, 1, 6, 17, 30, tzinfo=pytz.utc)  # Monday 12:30 PM ET
        next_dt = get_next_market_datetime(11, start)
        self.assertEqual(next_dt.strftime("%Y-%m-%d %H:%M %Z"), "2025-01-07 11:00 EST")


class MarketOpenTests(unittest.TestCase):
    """A run scheduled for a closed market wastes an LLM budget on stale data."""

    def test_a_weekend_is_closed(self):
        for day in (10, 11):  # Saturday, Sunday
            is_open, reason = is_market_open(_eastern(2026, 1, day, 11, 0))

            self.assertFalse(is_open)
            self.assertIn("weekend", reason.lower())

    def test_before_the_open_is_closed(self):
        is_open, reason = is_market_open(_eastern(2026, 1, 6, 9, 0))

        self.assertFalse(is_open)
        self.assertIn("opens at 9:30", reason)

    def test_the_opening_bell_counts_as_open(self):
        is_open, _reason = is_market_open(_eastern(2026, 1, 6, 9, 30))

        self.assertTrue(is_open)

    def test_after_the_close_is_closed(self):
        is_open, reason = is_market_open(_eastern(2026, 1, 6, 16, 30))

        self.assertFalse(is_open)
        self.assertIn("closed at 4:00 PM", reason)

    def test_the_closing_bell_still_counts_as_open(self):
        is_open, _reason = is_market_open(_eastern(2026, 1, 6, 16, 0))

        self.assertTrue(is_open)

    def test_omitting_the_time_asks_about_now(self):
        is_open, reason = is_market_open()

        self.assertIsInstance(is_open, bool)
        self.assertTrue(reason)


class ScheduleTests(unittest.TestCase):
    def test_an_hour_still_ahead_today_is_not_pushed_to_tomorrow(self):
        next_dt = get_next_market_datetime(15, _eastern(2026, 1, 6, 10, 0))

        self.assertEqual(next_dt.strftime("%Y-%m-%d %H:%M"), "2026-01-06 15:00")

    def test_a_weekend_is_skipped(self):
        next_dt = get_next_market_datetime(11, _eastern(2026, 1, 9, 12, 0))

        self.assertEqual(next_dt.strftime("%A"), "Monday")

    def test_a_holiday_is_skipped(self):
        next_dt = get_next_market_datetime(11, _eastern(2026, 7, 2, 12, 0))

        self.assertNotEqual(next_dt.strftime("%Y-%m-%d"), "2026-07-03")

    def test_an_hour_the_market_is_never_open_at_still_returns_a_time(self):
        """Ten days of searching is the cap; a time is returned regardless."""
        next_dt = get_next_market_datetime(3, _eastern(2026, 1, 6, 10, 0))

        self.assertEqual(next_dt.hour, 3)

    def test_omitting_the_start_schedules_from_now(self):
        self.assertIsNotNone(get_next_market_datetime(11))


class ValidationTests(unittest.TestCase):
    def test_a_single_hour_is_accepted(self):
        self.assertEqual(validate_market_hours("11"), (True, [11], ""))

    def test_several_hours_are_sorted_and_deduplicated(self):
        is_valid, hours, error = validate_market_hours("15, 11, 11")

        self.assertTrue(is_valid)
        self.assertEqual(hours, [11, 15])
        self.assertEqual(error, "")

    def test_the_market_bounds_are_inclusive(self):
        self.assertTrue(validate_market_hours("9")[0])
        self.assertTrue(validate_market_hours("16")[0])

    def test_an_hour_outside_market_hours_is_refused_by_name(self):
        is_valid, hours, error = validate_market_hours("3")

        self.assertFalse(is_valid)
        self.assertEqual(hours, [])
        self.assertIn("Hour 3", error)

    def test_a_blank_input_is_refused(self):
        for value in ("", "   ", None):
            is_valid, _hours, error = validate_market_hours(value)

            self.assertFalse(is_valid)
            self.assertIn("at least one", error)

    def test_a_list_of_only_separators_is_refused(self):
        is_valid, _hours, error = validate_market_hours(",,")

        self.assertFalse(is_valid)
        self.assertIn("at least one", error)

    def test_a_non_numeric_hour_is_refused_with_an_example(self):
        is_valid, _hours, error = validate_market_hours("noon")

        self.assertFalse(is_valid)
        self.assertIn("11,13", error)


class DisplayTests(unittest.TestCase):
    def test_hours_are_rendered_in_wall_clock_terms(self):
        info = format_market_hours_info([9, 15])

        self.assertEqual(info["formatted_hours"], "9:00 AM and 3:00 PM")

    def test_midnight_and_noon_are_named(self):
        self.assertIn("12:00 AM", format_market_hours_info([0])["formatted_hours"])
        self.assertIn("12:00 PM", format_market_hours_info([12])["formatted_hours"])

    def test_each_hour_carries_its_next_execution(self):
        info = format_market_hours_info([11])

        self.assertEqual(len(info["next_executions"]), 1)
        self.assertEqual(info["next_executions"][0]["hour"], 11)
        self.assertTrue(info["next_executions"][0]["next_formatted"])

    def test_no_hours_reports_an_error_rather_than_an_empty_schedule(self):
        self.assertIn("error", format_market_hours_info([]))


if __name__ == "__main__":
    unittest.main()
