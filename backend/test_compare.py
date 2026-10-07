"""compare.py 的离线单测：只用标准库，python3 -m unittest 即可。

覆盖交班条：
- 没办结的读数不许进样本；
- 默认窗 + 跨零夜窗归窗；单侧空着昼夜差留空；
- 大差串在默认窗下差值大，收到窗外后变平或留空、窗外点计数；
- 对照窗合法性（相交、跨零、边界相接）。
"""
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from compare import (
    DEFAULT_WINDOW,
    WindowError,
    compare_strings,
    in_night,
    normalize_window,
    phase_of,
)

SH = ZoneInfo("Asia/Shanghai")


def scan(i, code, ff, ts, status="done"):
    return {"id": i, "string_code": code, "fill_factor": ff, "status": status, "created_at": ts}


def t(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=SH)


def find(rows, code):
    return next(r for r in rows if r["string_code"] == code)


class WindowTests(unittest.TestCase):
    def test_default_window_valid(self):
        self.assertEqual(normalize_window(6, 18, 18, 30), (6.0, 18.0, 18.0, 30.0))

    def test_touching_edges_allowed(self):
        # 昼 [6,18) 与夜 [18,30) 在 18 点相接，左闭右开不算相交。
        self.assertEqual(normalize_window(6, 18, 18, 30), (6.0, 18.0, 18.0, 30.0))

    def test_overlap_before_midnight_rejected(self):
        with self.assertRaises(WindowError):
            normalize_window(10, 20, 19, 23)

    def test_overlap_after_midnight_rejected(self):
        with self.assertRaises(WindowError):
            normalize_window(2, 10, 20, 28)  # 夜跨零到次日4点，压住昼窗起点

    def test_bad_order_and_span(self):
        with self.assertRaises(WindowError):
            normalize_window(18, 6, 18, 30)   # 昼起止颠倒
        with self.assertRaises(WindowError):
            normalize_window(6, 18, 18, 40)   # 夜跨得超过一圈
        with self.assertRaises(WindowError):
            normalize_window("x", 18, 18, 30)  # 非数字

    def test_cross_midnight_membership(self):
        # 夜 [18,30)：23 点在内、02 点（跨零）在内、中午在外。
        self.assertTrue(in_night(23, 18, 30))
        self.assertTrue(in_night(2, 18, 30))
        self.assertFalse(in_night(12, 18, 30))
        self.assertEqual(phase_of(18, DEFAULT_WINDOW), "night")  # 18 点不属于昼窗
        self.assertEqual(phase_of(17.99, DEFAULT_WINDOW), "day")

    def test_non_crossing_night_window(self):
        # 夜窗 [1,5) 不跨零：远处的昼窗 [8,12) 合法，不能被误判相交。
        self.assertEqual(normalize_window(8, 12, 1, 5), (8.0, 12.0, 1.0, 5.0))
        self.assertTrue(in_night(2, 1, 5))
        self.assertFalse(in_night(6, 1, 5))
        self.assertFalse(in_night(23, 1, 5))


class CompareTests(unittest.TestCase):
    def setUp(self):
        # 种子同款：A 串昼夜都有、B 串大差且混一条 pending、C 串只有夜样本。
        self.scans = [
            scan(1, "阵列A-串03", 0.78, t(6, 12)),
            scan(2, "阵列A-串03", 0.80, t(6, 13)),
            scan(3, "阵列A-串03", 0.74, t(6, 23)),
            scan(4, "阵列B-串11", 0.61, t(6, 12, 30)),
            scan(5, "阵列B-串11", 0.63, t(7, 11)),
            scan(6, "阵列B-串11", 0.77, t(7, 2)),
            scan(7, "阵列B-串11", 0.60, t(7, 12), status="pending"),  # 未办结
            scan(8, "阵列C-串05", 0.73, t(7, 1)),
        ]

    def test_pending_never_sampled(self):
        rows = compare_strings(self.scans)
        b = find(rows, "阵列B-串11")
        all_ids = [p["id"] for p in b["day"]["samples"] + b["night"]["samples"]]
        self.assertNotIn(7, all_ids)
        # 昼均 = (0.61+0.63)/2，夜 = 0.77。
        self.assertAlmostEqual(b["day"]["mean_ff"], 0.62, places=4)
        self.assertAlmostEqual(b["night"]["mean_ff"], 0.77, places=4)

    def test_big_diff_under_default_window(self):
        b = find(compare_strings(self.scans), "阵列B-串11")
        self.assertLessEqual(b["diff"], -0.1)  # 故意拉大：差得离谱

    def test_one_side_empty_diff_is_none(self):
        c = find(compare_strings(self.scans), "阵列C-串05")
        self.assertEqual(c["day"]["count"], 0)
        self.assertEqual(c["night"]["count"], 1)
        self.assertIsNone(c["diff"])  # 空着不报数

    def test_shrink_day_window_to_nowhere_diff_empties(self):
        # 把 B 串昼窗收到没有任何点的 [14,15)，夜窗照旧：昼侧空→差值留空，
        # 原本在昼窗里的 0.61/0.63 变成窗外点。
        rows = compare_strings(
            self.scans, {"阵列B-串11": normalize_window(14, 15, 18, 30)}
        )
        b = find(rows, "阵列B-串11")
        self.assertEqual(b["day"]["count"], 0)
        self.assertEqual(b["night"]["count"], 1)
        self.assertIsNone(b["diff"])
        self.assertEqual(b["outside_count"], 2)

    def test_shrink_window_flattens_diff(self):
        # 另一组：白天一堆被拉低的点，夜里正常；窄窗只框住两侧接近的点后差应变平。
        scans = [
            scan(1, "X", 0.62, t(6, 10)),
            scan(2, "X", 0.61, t(6, 12)),
            scan(3, "X", 0.71, t(6, 12, 45)),  # 中午里夹一个正常值
            scan(4, "X", 0.72, t(6, 22)),
        ]
        big = find(compare_strings(scans), "X")
        self.assertLess(big["diff"], -0.05)
        flat_rows = compare_strings(scans, {"X": normalize_window(12.5, 14, 21, 23)})
        flat = find(flat_rows, "X")
        # 昼只剩 0.71、夜只剩 0.72，差值收敛到 0.01 以内；其余两点落到窗外。
        self.assertEqual(flat["day"]["count"], 1)
        self.assertEqual(flat["night"]["count"], 1)
        self.assertEqual(flat["outside_count"], 2)
        self.assertAlmostEqual(flat["diff"], -0.01, places=4)

    def test_utc_timestamps_classified_in_shanghai(self):
        # 04:00UTC = 12:00 上海 → 昼；18:00UTC = 次日02:00 上海 → 夜。
        scans = [
            scan(1, "Z", 0.8, datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc)),
            scan(2, "Z", 0.7, datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)),
        ]
        z = find(compare_strings(scans), "Z")
        self.assertEqual(z["day"]["count"], 1)
        self.assertEqual(z["night"]["count"], 1)
        self.assertAlmostEqual(z["diff"], 0.1, places=4)

    def test_per_string_window_leaves_other_string_on_default(self):
        rows = compare_strings(
            self.scans, {"阵列B-串11": normalize_window(14, 15, 18, 30)}
        )
        a = find(rows, "阵列A-串03")
        self.assertEqual((a["window"]["day_start"], a["window"]["day_end"]), (6.0, 18.0))


if __name__ == "__main__":
    unittest.main()
