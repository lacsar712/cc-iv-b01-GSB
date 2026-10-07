import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
-- 每组串只保留一笔对照窗：主键即组串编号，并发改窗靠 upsert 串行化，最后一笔覆盖。
-- night_end 可大于 24（最大 30），表示夜窗跨到次日凌晨。
CREATE TABLE IF NOT EXISTS compare_windows (
    string_code text PRIMARY KEY,
    day_start double precision NOT NULL,
    day_end double precision NOT NULL,
    night_start double precision NOT NULL,
    night_end double precision NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT compare_windows_range_chk
        CHECK (day_start >= 0 AND day_end <= 24 AND day_start < day_end
               AND night_start >= 0 AND night_start < 24
               AND night_end > night_start AND night_end <= 30)
);
-- 兼容旧开发卷：夜窗两列是后加的。
ALTER TABLE compare_windows ADD COLUMN IF NOT EXISTS night_start double precision;
ALTER TABLE compare_windows ADD COLUMN IF NOT EXISTS night_end double precision;
UPDATE compare_windows SET night_start = 18, night_end = 30
    WHERE night_start IS NULL OR night_end IS NULL;
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
