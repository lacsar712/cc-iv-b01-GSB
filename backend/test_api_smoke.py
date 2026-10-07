"""接口级冒烟测试：用内存假库顶替 PostgreSQL，python3 test_api_smoke.py 运行。

覆盖：
- watcher 只读：GET 放行、PUT 对照窗 403、提交 403；
- scanner 改窗：合法窗写入、非法窗 400 不落库；
- 同一组串连改两笔只保留最后一笔窗宽（upsert 一行）；
- GET /api/compare：pending 不进样本、收窗后大差串变留空、带口径；
- 昼夜差只随窗即时重算，不接受任何手填差值。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import db

SH = ZoneInfo("Asia/Shanghai")


class FakeResult:
    def __init__(self, rows=None, one=None):
        self._rows = rows if rows is not None else ([one] if one is not None else [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _mk_scan(i, code, ff, status, ts):
    return {
        "id": i, "string_code": code, "voc_v": 40.0, "isc_a": 9.0,
        "fill_factor": ff, "status": status, "verdict": None, "reason": None,
        "created_by": "scanner", "created_at": ts, "processed_at": ts if status == "done" else None,
    }


class FakeConn:
    def __init__(self):
        self.scans = [
            _mk_scan(1, "阵列A-串03", 0.78, "done", datetime(2026, 10, 6, 12, tzinfo=SH)),
            _mk_scan(2, "阵列A-串03", 0.80, "done", datetime(2026, 10, 6, 13, tzinfo=SH)),
            _mk_scan(3, "阵列A-串03", 0.74, "done", datetime(2026, 10, 6, 23, tzinfo=SH)),
            _mk_scan(4, "阵列B-串11", 0.61, "done", datetime(2026, 10, 6, 12, 30, tzinfo=SH)),
            _mk_scan(5, "阵列B-串11", 0.63, "done", datetime(2026, 10, 7, 11, tzinfo=SH)),
            _mk_scan(6, "阵列B-串11", 0.77, "done", datetime(2026, 10, 7, 2, tzinfo=SH)),
            _mk_scan(7, "阵列B-串11", 0.60, "pending", datetime(2026, 10, 7, 12, tzinfo=SH)),
        ]
        self.windows = {}  # string_code -> row dict
        self._next = 8

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        s = " ".join(sql.split())

        if s.startswith(("CREATE", "ALTER", "DROP", "UPDATE")):
            return FakeResult()
        if s.startswith("SELECT COUNT(*)"):
            return FakeResult(one={"n": len(self.scans)})

        if "INSERT INTO iv_scans" in s:
            if "RETURNING" in s:
                code, voc, isc, ff, user, ts = params
                row = _mk_scan(self._next, code, ff, "pending", ts)
                row.update(voc_v=voc, isc_a=isc, created_by=user)
                self._next += 1
                self.scans.append(row)
                return FakeResult(one=dict(row))
            if len(params) == 1:
                # 种子里的 pending 干扰点
                self.scans.append(_mk_scan(self._next, "阵列B-串11", 0.60, "pending", params[0]))
                self._next += 1
                return FakeResult()
            # 种子 done 插入
            code, voc, isc, ff, verdict, reason, ca, pa = params
            self.scans.append(_mk_scan(self._next, code, ff, "done", ca))
            self._next += 1
            return FakeResult()

        if "INSERT INTO compare_windows" in s:
            code, ds, de, ns, ne, user, ts = params
            row = {
                "string_code": code, "day_start": ds, "day_end": de,
                "night_start": ns, "night_end": ne, "updated_by": user, "updated_at": ts,
            }
            self.windows[code] = row  # upsert：同串覆盖，永远一行
            return FakeResult(one=dict(row))

        if "FROM compare_windows" in s:
            rows = [dict(r) for r in self.windows.values()]
            if "updated_by" in s:
                rows.sort(key=lambda r: r["string_code"])
            return FakeResult(rows=rows)

        if "FROM iv_scans" in s:
            return FakeResult(rows=[dict(r) for r in self.scans])

        raise AssertionError("unexpected SQL: " + s[:120])


DB = FakeConn()
db.connect = lambda: DB

import api  # noqa: E402  —— 顶层 seed() 现在跑在假库上
from litestar.testing import TestClient  # noqa: E402


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def find(strings, code):
    return next(s for s in strings if s["string_code"] == code)


def main():
    with TestClient(app=api.app) as c:
        sc = login(c, "scanner", "scan123456")
        wc = login(c, "watcher", "watch123456")

        # 1) 未登录 401
        assert c.get("/api/compare").status_code == 401

        # 2) watcher 可看不可改、不可报送
        r = c.get("/api/compare", headers=wc)
        assert r.status_code == 200
        body = r.json()
        assert "criteria" in body and body["criteria"]["sample_rule"]
        b = find(body["strings"], "阵列B-串11")
        ids = [p["id"] for p in b["day"]["samples"] + b["night"]["samples"]]
        assert 7 not in ids, "pending 读数混进了样本"
        assert b["diff"] <= -0.1, b["diff"]
        r = c.put("/api/compare-windows/x", headers=wc, json={"day_start": 1, "day_end": 2, "night_start": 3, "night_end": 4})
        assert r.status_code == 403, r.status_code
        r = c.post("/api/logs", headers=wc, json={"string_code": "x", "voc_v": 1, "isc_a": 1, "fill_factor": 0.8})
        assert r.status_code == 403

        # 3) 非法窗 400 且不落库
        r = c.put("/api/compare-windows/阵列B-串11", headers=sc,
                  json={"day_start": 20, "day_end": 22, "night_start": 21, "night_end": 23})
        assert r.status_code == 400, r.text
        assert "阵列B-串11" not in DB.windows

        # 4) scanner 连改两笔：只留最后一笔窗宽
        for win in [
            {"day_start": 8, "day_end": 17, "night_start": 19, "night_end": 29},
            {"day_start": 14, "day_end": 15, "night_start": 18, "night_end": 30},
        ]:
            r = c.put("/api/compare-windows/阵列B-串11", headers=sc, json=win)
            assert r.status_code in (200, 201), r.text
        rows = c.get("/api/compare-windows", headers=sc).json()["windows"]
        mine = [w for w in rows if w["string_code"] == "阵列B-串11"]
        assert len(mine) == 1, f"同串出现 {len(mine)} 笔窗"
        assert (mine[0]["day_start"], mine[0]["day_end"]) == (14.0, 15.0)

        # 5) 按新窗重算：白天点全部落到窗外 → 昼侧空、差值留空
        body = c.get("/api/compare", headers=sc).json()
        b = find(body["strings"], "阵列B-串11")
        assert b["day"]["count"] == 0 and b["night"]["count"] == 1
        assert b["diff"] is None
        assert b["outside_count"] == 2, b["outside_count"]

        # 6) A 串没设过窗，仍走默认窗
        a = find(body["strings"], "阵列A-串03")
        assert (a["window"]["day_start"], a["window"]["day_end"]) == (6.0, 18.0)
        assert abs(a["diff"] - (0.79 - 0.74)) < 1e-9, a["diff"]

        print("API SMOKE OK")


if __name__ == "__main__":
    main()
