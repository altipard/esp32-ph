# Host-Tests fuer batt.py (pure Python, kein MicroPython noetig).
#
# Ausfuehren:  python3 -m unittest discover -s tests/firmware
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "internal", "assets", "firmware"))

import batt  # noqa: E402


class TestVoltageToPercent(unittest.TestCase):
    def test_full_cell(self):
        self.assertEqual(batt.voltage_to_percent(4.20), 100)

    def test_above_full_clamps(self):
        self.assertEqual(batt.voltage_to_percent(4.30), 100)

    def test_empty_cell(self):
        self.assertEqual(batt.voltage_to_percent(3.30), 0)

    def test_below_empty_clamps(self):
        self.assertEqual(batt.voltage_to_percent(3.10), 0)

    def test_midpoint_follows_liion_curve_not_linear(self):
        # 3.85 V Ruhespannung ~= halbvoll. Linear (4.2..3.3) waere 61 % —
        # die OCV-Kurve liegt bei ~55 %.
        pct = batt.voltage_to_percent(3.85)
        self.assertTrue(50 <= pct <= 60, "3.85V -> %s%%" % pct)

    def test_plateau_region(self):
        # Im Li-Ion-Plateau (3.6..3.7 V) ist die Zelle fast leer — linear
        # wuerde hier noch 33..44 % anzeigen.
        self.assertTrue(batt.voltage_to_percent(3.70) <= 32)
        self.assertTrue(batt.voltage_to_percent(3.60) <= 18)

    def test_interpolates_between_table_points(self):
        lo = batt.voltage_to_percent(3.80)
        mid = batt.voltage_to_percent(3.825)
        hi = batt.voltage_to_percent(3.85)
        self.assertTrue(lo < mid < hi)

    def test_monotonic(self):
        prev = -1
        v = 3.30
        while v <= 4.20:
            pct = batt.voltage_to_percent(v)
            self.assertGreaterEqual(pct, prev, "nicht monoton bei %.2fV" % v)
            prev = pct
            v = round(v + 0.01, 2)

    def test_returns_int_in_range(self):
        for v in (3.3, 3.55, 3.81, 4.0, 4.2):
            pct = batt.voltage_to_percent(v)
            self.assertIsInstance(pct, int)
            self.assertTrue(0 <= pct <= 100)


class TestPlausible(unittest.TestCase):
    def test_floating_pin_low(self):
        self.assertFalse(batt.is_plausible(0.4))

    def test_floating_pin_high(self):
        self.assertFalse(batt.is_plausible(4.6))

    def test_real_cell(self):
        for v in (3.0, 3.3, 3.7, 4.2, 4.35):
            self.assertTrue(batt.is_plausible(v), "%.2fV muss plausibel sein" % v)

    def test_just_outside_window(self):
        self.assertFalse(batt.is_plausible(2.99))
        self.assertFalse(batt.is_plausible(4.36))


if __name__ == "__main__":
    unittest.main()
