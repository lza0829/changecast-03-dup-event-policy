from __future__ import annotations

from datetime import datetime, timezone

from app.db import connect

STREAM_SEEDS = [
    {
        "code": "member_profile",
        "title": "会员档案变更",
        "domain": "member",
        "entity_key_name": "member_id",
        "payload_fields": ["display_name", "tier", "region"],
        "fixture": "member_profile.json",
        "enabled": 1,
        "duplicate_policy": "idempotent",
        "conflict_outcome": "rollback_batch",
    },
    {
        "code": "store_archive",
        "title": "门店档案变更",
        "domain": "store",
        "entity_key_name": "store_id",
        "payload_fields": ["name", "status", "city"],
        "fixture": "store_archive.json",
        "enabled": 1,
        "duplicate_policy": "idempotent",
        "conflict_outcome": "rollback_batch",
    },
    {
        "code": "channel_attr",
        "title": "渠道属性变更",
        "domain": "channel",
        "entity_key_name": "channel_code",
        "payload_fields": ["label", "active"],
        "fixture": "channel_attr.json",
        "enabled": 1,
        "duplicate_policy": "idempotent",
        "conflict_outcome": "rollback_batch",
    },
    {
        "code": "dup_demo",
        "title": "重复事件演示流",
        "domain": "demo",
        "entity_key_name": "cust_id",
        "payload_fields": ["level", "owner", "note"],
        "fixture": "dup_demo.json",
        "enabled": 1,
        # 策略甲：幂等 + 冲突隔离。整批失败 / 策略乙可在投影流页切换后演示。
        "duplicate_policy": "idempotent",
        "conflict_outcome": "quarantine",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """Idempotent ALTER TABLE for SQLite (no IF NOT EXISTS on ADD COLUMN)."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    import json

    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_streams(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                entity_key_name TEXT NOT NULL,
                payload_fields_json TEXT NOT NULL,
                fixture TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                watermark_seq INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_batches(
                id INTEGER PRIMARY KEY,
                batch_no TEXT NOT NULL UNIQUE,
                stream_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                source_events INTEGER NOT NULL,
                applied_upsert INTEGER NOT NULL,
                applied_delete INTEGER NOT NULL,
                skipped_duplicate INTEGER NOT NULL,
                projected_alive INTEGER NOT NULL,
                projected_deleted INTEGER NOT NULL,
                max_seq INTEGER NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS projection_rows(
                id INTEGER PRIMARY KEY,
                stream_id INTEGER NOT NULL,
                entity_key TEXT NOT NULL,
                payload_json TEXT,
                applied_seq INTEGER NOT NULL,
                applied_event_id TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(stream_id, entity_key),
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS applied_events(
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                stream_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                op TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES project_batches(id),
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS ooo_buffer(
                id INTEGER PRIMARY KEY,
                stream_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                op TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                payload_json TEXT,
                buffered_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                FOREIGN KEY(stream_id) REFERENCES project_streams(id)
            );
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conflict_events(
                id INTEGER PRIMARY KEY,
                stream_id INTEGER NOT NULL,
                batch_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                op TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_time TEXT NOT NULL,
                payload_json TEXT,
                payload_digest TEXT NOT NULL,
                prior_digest TEXT NOT NULL,
                conflict_kind TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(stream_id) REFERENCES project_streams(id),
                FOREIGN KEY(batch_id) REFERENCES project_batches(id)
            );
            """
        )

        # --- migrations for databases created before this feature ---------
        _ensure_column(conn, "project_streams", "duplicate_policy",
                       "duplicate_policy TEXT NOT NULL DEFAULT 'idempotent'")
        _ensure_column(conn, "project_streams", "conflict_outcome",
                       "conflict_outcome TEXT NOT NULL DEFAULT 'rollback_batch'")
        _ensure_column(conn, "project_batches", "payload_conflicts",
                       "payload_conflicts INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "project_batches", "batch_rejections",
                       "batch_rejections INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "project_batches", "quarantined_events",
                       "quarantined_events INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "project_batches", "duplicate_policy",
                       "duplicate_policy TEXT")
        _ensure_column(conn, "project_batches", "conflict_outcome",
                       "conflict_outcome TEXT")
        _ensure_column(conn, "project_batches", "reject_reason",
                       "reject_reason TEXT")
        _ensure_column(conn, "applied_events", "payload_digest",
                       "payload_digest TEXT")
        _ensure_column(conn, "applied_events", "prior_digest",
                       "prior_digest TEXT")

        if conn.execute("SELECT COUNT(*) c FROM project_streams").fetchone()["c"] == 0:
            now = _now()
            for s in STREAM_SEEDS:
                conn.execute(
                    """
                    INSERT INTO project_streams(
                        code, title, domain, entity_key_name, payload_fields_json,
                        fixture, enabled, watermark_seq, duplicate_policy,
                        conflict_outcome, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        s["code"],
                        s["title"],
                        s["domain"],
                        s["entity_key_name"],
                        json.dumps(s["payload_fields"], ensure_ascii=False),
                        s["fixture"],
                        s["enabled"],
                        0,
                        s["duplicate_policy"],
                        s["conflict_outcome"],
                        now,
                    ),
                )
        else:
            # Backfill policy columns on pre-feature rows, then make sure the
            # demo stream exists when reseeding against an older database.
            conn.execute(
                "UPDATE project_streams SET duplicate_policy='idempotent' "
                "WHERE duplicate_policy IS NULL OR duplicate_policy=''"
            )
            conn.execute(
                "UPDATE project_streams SET conflict_outcome='rollback_batch' "
                "WHERE conflict_outcome IS NULL OR conflict_outcome=''"
            )
            existing = {r["code"] for r in conn.execute("SELECT code FROM project_streams")}
            now = _now()
            for s in STREAM_SEEDS:
                if s["code"] in existing:
                    continue
                conn.execute(
                    """
                    INSERT INTO project_streams(
                        code, title, domain, entity_key_name, payload_fields_json,
                        fixture, enabled, watermark_seq, duplicate_policy,
                        conflict_outcome, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        s["code"], s["title"], s["domain"], s["entity_key_name"],
                        json.dumps(s["payload_fields"], ensure_ascii=False), s["fixture"],
                        s["enabled"], 0, s["duplicate_policy"], s["conflict_outcome"], now,
                    ),
                )

        if conn.execute("SELECT COUNT(*) c FROM settings").fetchone()["c"] == 0:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("apply_order", "by_seq"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("duplicate_policy", "idempotent"),
            )
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?)",
                ("tombstone_mode", "soft_delete"),
            )
        conn.commit()
    finally:
        conn.close()
