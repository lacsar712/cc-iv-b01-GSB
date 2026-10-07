import os
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from compare import compute_day_night, upsert_window
from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}

BJ = timezone(timedelta(hours=8))


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
            today = datetime.now(BJ).date()

            def ts(day_offset: int, hour: int):
                return datetime.combine(
                    today + timedelta(days=day_offset),
                    datetime.min.time(),
                    tzinfo=BJ,
                ).replace(hour=hour)

            # 白天 10 点与夜里 22 点各留若干办结点，供昼夜差专页对照
            rows = [
                ("阵列A-串03", 41.2, 9.1, 0.78, ts(-2, 10), "合格"),
                ("阵列A-串03", 41.0, 9.0, 0.79, ts(-1, 10), "合格"),
                ("阵列A-串03", 40.9, 8.9, 0.77, ts(0, 10), "合格"),
                ("阵列A-串03", 40.5, 8.7, 0.72, ts(-2, 22), "合格"),
                ("阵列A-串03", 40.4, 8.6, 0.71, ts(-1, 22), "衰减"),
                ("阵列B-串11", 38.0, 8.4, 0.61, ts(-2, 10), "衰减"),
                ("阵列B-串11", 38.2, 8.5, 0.63, ts(-1, 10), "衰减"),
                ("阵列B-串11", 38.1, 8.4, 0.62, ts(0, 10), "衰减"),
                ("阵列B-串11", 37.8, 8.2, 0.59, ts(-2, 22), "衰减"),
                ("阵列B-串11", 37.6, 8.1, 0.58, ts(-1, 22), "衰减"),
            ]
            for code, voc, isc, ff, created, expect in rows:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, created, created),
                )
        wn = conn.execute("SELECT COUNT(*) AS n FROM compare_windows").fetchone()["n"]
        if wn == 0:
            today = datetime.now(BJ).date()
            now = datetime.now(timezone.utc)
            # 阵列C-串05 只有窗、没有读数，用来展示样本落空时昼夜差留空
            windows = [
                ("阵列A-串03", -2, 3),
                ("阵列B-串11", -2, 3),
                ("阵列C-串05", 0, 1),
            ]
            for code, offset, days in windows:
                conn.execute(
                    """INSERT INTO compare_windows
                       (string_code, start_date, days, updated_by, updated_at)
                       VALUES (%s,%s,%s,'scanner',%s)""",
                    (code, today + timedelta(days=offset), days, now),
                )
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


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可提交IV扫描")
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


@get("/api/day-night-diff")
async def day_night_diff(request: Request) -> list:
    # 扫描员与巡视都可看；口径固定，谁都不能在返回上手改。
    need_login(request)
    with connect() as conn:
        return compute_day_night(conn)


@put("/api/compare-windows")
async def set_compare_window(request: Request) -> dict:
    # 只有扫描员能调对照窗；巡视口令只读，改窗与报送一律拒绝。
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="巡视账号只读，不能调对照窗")
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    raw_date = (data.get("start_date") or "").strip()
    try:
        start = date.fromisoformat(raw_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="起始日期格式应为 YYYY-MM-DD")
    try:
        days = int(data.get("days"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="窗宽必须是整数天数")
    if not 1 <= days <= 366:
        raise HTTPException(status_code=400, detail="窗宽须在 1 至 366 天之间")
    with connect() as conn:
        row = upsert_window(conn, code, start, days, user["username"])
        conn.commit()
        return row


app = Litestar(route_handlers=[health, login, list_logs, create_log,
                               day_night_diff, set_compare_window])
