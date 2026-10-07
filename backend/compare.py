"""昼夜填充因子对照：钟点归窗、按窗重算昼夜差。

纯函数、不碰数据库，方便离线单测。
- 昼窗 [day_start, day_end)，当天一段；
- 夜窗 [night_start, night_end)，允许跨零点：终点可填到 30，例如 18→30 表示
  [18:00,24:00) ∪ [00:00,06:00)；
- 只有 status='done'（已办结）的读数能进样本，pending 一律剔除；
- 落在昼窗、夜窗之外的钟点视为窗外点，不进任何一侧；
- 昼夜差 = 昼窗 FF 均值 - 夜窗 FF 均值；任一侧空着则差值为 None（空着不报数）；
- 时间戳统一按上海当地钟点归窗。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DAY_START = 6.0
DEFAULT_DAY_END = 18.0
DEFAULT_NIGHT_START = 18.0
DEFAULT_NIGHT_END = 30.0
DEFAULT_WINDOW = (
    DEFAULT_DAY_START,
    DEFAULT_DAY_END,
    DEFAULT_NIGHT_START,
    DEFAULT_NIGHT_END,
)
CRITERIA = {
    "tz": "Asia/Shanghai",
    "day_window": f"[{DEFAULT_DAY_START:g}:00, {DEFAULT_DAY_END:g}:00)",
    "night_window": f"[{DEFAULT_NIGHT_START:g}:00, {DEFAULT_NIGHT_END - 24:g}:00)（终点可填到30，跨零点）",
    "sample_rule": "仅已办结(status=done)读数进入样本，待处理读数一律不进样本",
    "outside_rule": "钟点落在昼窗与夜窗之外的读数为窗外点，不计入任一侧",
    "diff_rule": "昼夜差 = 昼窗填充因子均值 − 夜窗填充因子均值；任一侧无样本则留空",
    "recalc_rule": "昼夜差始终按各串当前对照窗即时重算，总表手填的一列数字不作为结果",
    "window_rule": "昼窗与夜窗不得相交且各自非空；每组串只保留一笔最新对照窗",
}


class WindowError(ValueError):
    """对照窗参数不合法。"""


def _num(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        raise WindowError("对照窗起止必须是数字（小时）")


def normalize_window(day_start, day_end, night_start, night_end) -> tuple[float, float, float, float]:
    """校验并归一化昼/夜窗，返回 (ds,de,ns,ne)；非法抛 WindowError。

    夜窗终点可大于 24（最多到 night_start+24、且不超过 30），表示跨零点。
    """
    ds, de = _num(day_start), _num(day_end)
    ns, ne = _num(night_start), _num(night_end)
    if not (0.0 <= ds < de <= 24.0):
        raise WindowError("昼窗须满足 0 ≤ 起点 < 终点 ≤ 24")
    if not (0.0 <= ns < 24.0):
        raise WindowError("夜窗起点须落在 [0, 24) 内")
    if not (ns < ne <= ns + 24.0) or ne > 30.0:
        raise WindowError("夜窗终点须晚于起点，且跨零点后不超过次日该时刻（最大30）")

    def overlap(a0, a1, b0, b1) -> bool:
        return a0 < b1 and b0 < a1

    # 夜窗拆成 [ns, min(ne,24)) 与跨零段 [0,ne-24)，分别和昼窗判交。
    seg1_end = min(ne, 24.0)
    if overlap(ds, de, ns, seg1_end):
        raise WindowError("昼窗与夜窗相交，同一钟点不能既属昼又属夜")
    if ne > 24.0 and overlap(ds, de, 0.0, ne - 24.0):
        raise WindowError("昼窗与跨零的夜窗相交，同一钟点不能既属昼又属夜")
    return tuple(round(x, 2) for x in (ds, de, ns, ne))


def local_hour(ts: datetime) -> float:
    """把时间戳换算成上海当地钟点（0~24 的小时小数）。"""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    local = ts.astimezone(TZ)
    return (
        local.hour
        + local.minute / 60.0
        + local.second / 3600.0
        + local.microsecond / 3_600_000_000.0
    )


def in_day(hour: float, ds: float, de: float) -> bool:
    return ds <= hour < de


def in_night(hour: float, ns: float, ne: float) -> bool:
    # 跨零点：把钟点抬到以 ns 为起点的 24 小时标尺上再判区间。
    t = hour if hour >= ns else hour + 24.0
    return ns <= t < ne


def phase_of(hour: float, window) -> str | None:
    ds, de, ns, ne = window
    if in_day(hour, ds, de):
        return "day"
    if in_night(hour, ns, ne):
        return "night"
    return None  # 窗外


def _empty_side() -> dict:
    return {"count": 0, "mean_ff": None, "first_at": None, "last_at": None, "samples": []}


def _summary(rows: list[dict]) -> dict:
    if not rows:
        return _empty_side()
    ffs = [float(r["fill_factor"]) for r in rows]
    times = sorted(r["created_at"] for r in rows)
    return {
        "count": len(rows),
        "mean_ff": round(sum(ffs) / len(ffs), 4),
        "first_at": times[0],
        "last_at": times[-1],
        "samples": [
            {"id": r["id"], "fill_factor": float(r["fill_factor"]), "at": r["created_at"]}
            for r in sorted(rows, key=lambda r: r["id"])
        ],
    }


def compare_strings(scans, windows: dict | None = None) -> list[dict]:
    """按串汇总昼夜样本与昼夜差。

    scans: 可迭代 dict，含 id/string_code/fill_factor/status/created_at。
    windows: {string_code: (ds,de,ns,ne)}；缺失的串用默认昼夜窗。
    结果按组串编号排序，只输出至少有一条 done 样本的组串。
    """
    windows = windows or {}
    grouped: dict[str, dict] = {}
    for row in scans:
        if row.get("status") != "done":
            continue  # 没办结的读数不许进样本
        code = row["string_code"]
        window = windows.get(code, DEFAULT_WINDOW)
        bucket = grouped.setdefault(
            code, {"day": [], "night": [], "outside": 0, "window": window}
        )
        phase = phase_of(local_hour(row["created_at"]), window)
        if phase is None:
            bucket["outside"] += 1  # 收到窗外：不进样本
        else:
            bucket[phase].append(row)

    out = []
    for code in sorted(grouped):
        bucket = grouped[code]
        day = _summary(bucket["day"])
        night = _summary(bucket["night"])
        if day["mean_ff"] is None or night["mean_ff"] is None:
            diff = None  # 一侧空着：差值留空，不硬凑
        else:
            diff = round(day["mean_ff"] - night["mean_ff"], 4)
        ds, de, ns, ne = bucket["window"]
        out.append(
            {
                "string_code": code,
                "window": {"day_start": ds, "day_end": de, "night_start": ns, "night_end": ne},
                "day": day,
                "night": night,
                "outside_count": bucket["outside"],
                "diff": diff,
            }
        )
    return out
