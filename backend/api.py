import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge
from compare import (
    CRITERIA,
    DEFAULT_DAY_END,
    DEFAULT_DAY_START,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    WindowError,
    compare_strings,
    normalize_window,
)

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            sh = ZoneInfo("Asia/Shanghai")

            def at(day: int, hour: float, minute: int = 0):
                return datetime(2026, 10, day, int(hour), minute, tzinfo=sh)

            def done(code, voc, isc, ff, ts):
                verdict, reason = judge(ff)
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, ts, ts),
                )

            # 阵列A-串03：昼夜两侧都有办结读数，白天略高于夜里。
            done("阵列A-串03", 41.2, 9.1, 0.78, at(6, 12))
            done("阵列A-串03", 41.5, 9.2, 0.80, at(6, 13))
            done("阵列A-串03", 40.9, 9.0, 0.74, at(6, 23))
            # 阵列B-串11：白天被故意拉低、昼夜差偏大；另留一条待处理读数，不许进样本。
            done("阵列B-串11", 38.0, 8.4, 0.61, at(6, 12, 30))
            done("阵列B-串11", 37.6, 8.3, 0.63, at(7, 11))
            done("阵列B-串11", 39.1, 8.6, 0.77, at(7, 2))
            conn.execute(
                """INSERT INTO iv_scans
                   (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
                   VALUES ('阵列B-串11', 38.4, 8.4, 0.60, 'pending', 'scanner', %s)""",
                (at(7, 12),),
            )
            # 阵列C-串05：只有夜里办结读数，昼侧空着，昼夜差应留空。
            done("阵列C-串05", 40.0, 8.8, 0.73, at(7, 1))
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request, action: str = "提交IV扫描"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=f"仅扫描员可{action}")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


def _load_windows(conn) -> dict:
    rows = conn.execute(
        "SELECT string_code, day_start, day_end, night_start, night_end FROM compare_windows"
    ).fetchall()
    return {
        r["string_code"]: (
            float(r["day_start"]),
            float(r["day_end"]),
            float(r["night_start"]),
            float(r["night_end"]),
        )
        for r in rows
    }


_DEFAULT_WINDOW_PAYLOAD = {
    "day_start": DEFAULT_DAY_START,
    "day_end": DEFAULT_DAY_END,
    "night_start": DEFAULT_NIGHT_START,
    "night_end": DEFAULT_NIGHT_END,
}


@get("/api/compare-windows")
async def get_windows(request: Request) -> dict:
    """每串当前对照窗；没有手设过的串不出现，前端按默认窗显示。"""
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT string_code, day_start, day_end, night_start, night_end,
                      updated_by, updated_at
               FROM compare_windows ORDER BY string_code"""
        ).fetchall()
        return {"default": dict(_DEFAULT_WINDOW_PAYLOAD), "windows": [dump(r) for r in rows]}


@put("/api/compare-windows/{code:str}")
async def put_window(request: Request, code: str) -> dict:
    """扫描员调整某串对照窗；每组串只有一行，并发抢改时最后一笔覆盖，只留一笔窗宽。"""
    user = need_writer(request, action="调整对照窗")
    data = await request.json()
    try:
        ds, de, ns, ne = normalize_window(
            data.get("day_start"),
            data.get("day_end"),
            data.get("night_start"),
            data.get("night_end"),
        )
    except WindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO compare_windows
                   (string_code, day_start, day_end, night_start, night_end,
                    updated_by, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (string_code) DO UPDATE
                   SET day_start = EXCLUDED.day_start,
                       day_end = EXCLUDED.day_end,
                       night_start = EXCLUDED.night_start,
                       night_end = EXCLUDED.night_end,
                       updated_by = EXCLUDED.updated_by,
                       updated_at = EXCLUDED.updated_at
               RETURNING string_code, day_start, day_end, night_start, night_end,
                         updated_by, updated_at""",
            (code, ds, de, ns, ne, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/compare")
async def compare(request: Request) -> dict:
    """昼夜差专页数据：口径 + 按串昼夜差/样本点。结果一律按当前对照窗即时重算，
    不读总表里手填的任何一列数字。"""
    need_login(request)
    with connect() as conn:
        scans = conn.execute(
            """SELECT id, string_code, fill_factor, status, created_at
               FROM iv_scans ORDER BY id"""
        ).fetchall()
        windows = _load_windows(conn)
    rows = compare_strings([dict(r) for r in scans], windows)
    return {
        "criteria": CRITERIA,
        "default_window": dict(_DEFAULT_WINDOW_PAYLOAD),
        "strings": rows,
    }


app = Litestar(
    route_handlers=[health, login, list_logs, create_log, get_windows, put_window, compare]
)