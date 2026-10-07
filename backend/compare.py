"""昼夜差口径：从已办结读数按每个组串当前的对照窗实时重算。

- 只取 iv_scans.status = 'done' 的读数；pending 一律不进样本。
- 对照窗为北京时间日期区间 [start_date, start_date + days)。
- 白天 06:00-18:00（含 6 点、不含 18 点），其余为夜里。
- 昼夜差 = 白天均值 - 夜里均值；任一侧没有样本点则差值留空（None）。
"""
from datetime import date, datetime, timezone

DAY_HOUR_START = 6
DAY_HOUR_END = 18

_DIFF_SQL = """
SELECT w.string_code AS string_code,
       w.start_date  AS start_date,
       w.days        AS days,
       w.updated_by  AS updated_by,
       w.updated_at  AS updated_at,
       COUNT(s.fill_factor) FILTER (WHERE s.hh >= %s AND s.hh < %s) AS day_points,
       AVG(s.fill_factor)   FILTER (WHERE s.hh >= %s AND s.hh < %s) AS day_avg,
       COUNT(s.fill_factor) FILTER (WHERE s.hh < %s OR s.hh >= %s) AS night_points,
       AVG(s.fill_factor)   FILTER (WHERE s.hh < %s OR s.hh >= %s) AS night_avg
FROM compare_windows w
LEFT JOIN LATERAL (
    SELECT i.fill_factor AS fill_factor,
           EXTRACT(HOUR FROM i.created_at AT TIME ZONE 'Asia/Shanghai')::int AS hh,
           (i.created_at AT TIME ZONE 'Asia/Shanghai')::date AS d
    FROM iv_scans i
    WHERE i.string_code = w.string_code AND i.status = 'done'
) s ON s.d >= w.start_date AND s.d < w.start_date + w.days
GROUP BY w.string_code, w.start_date, w.days, w.updated_by, w.updated_at
ORDER BY w.string_code
"""


def _diff(day_avg, night_avg):
    if day_avg is None or night_avg is None:
        return None
    return round(day_avg - night_avg, 4)


def compute_day_night(conn) -> list[dict]:
    rows = conn.execute(
        _DIFF_SQL,
        (DAY_HOUR_START, DAY_HOUR_END, DAY_HOUR_START, DAY_HOUR_END,
         DAY_HOUR_START, DAY_HOUR_END, DAY_HOUR_START, DAY_HOUR_END),
    ).fetchall()
    out = []
    for r in rows:
        day_avg = r["day_avg"]
        night_avg = r["night_avg"]
        out.append({
            "string_code": r["string_code"],
            "start_date": r["start_date"].isoformat(),
            "days": r["days"],
            "day_points": r["day_points"],
            "night_points": r["night_points"],
            "day_avg": round(day_avg, 4) if day_avg is not None else None,
            "night_avg": round(night_avg, 4) if night_avg is not None else None,
            "diff": _diff(day_avg, night_avg),
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })
    return out


def upsert_window(conn, string_code: str, start_date: date, days: int,
                  username: str) -> dict:
    # string_code 主键：两人抢改同一串，后写只更新同一行，窗宽只留一笔。
    row = conn.execute(
        """INSERT INTO compare_windows (string_code, start_date, days, updated_by, updated_at)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (string_code) DO UPDATE
           SET start_date = EXCLUDED.start_date,
               days       = EXCLUDED.days,
               updated_by = EXCLUDED.updated_by,
           updated_at   = EXCLUDED.updated_at
           RETURNING string_code, start_date, days, updated_by, updated_at""",
        (string_code, start_date, days, username, datetime.now(timezone.utc)),
    ).fetchone()
    return {
        "string_code": row["string_code"],
        "start_date": row["start_date"].isoformat(),
        "days": row["days"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"].isoformat(),
    }
