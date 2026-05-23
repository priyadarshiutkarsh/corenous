"""SQLite persistence for memories, vectors, vault entries, and config."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..turboquant.encoder import CompressedVector, to_bytes, from_bytes, BLOB_SIZE

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS memories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT    NOT NULL,
    text_snippet TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    app_name     TEXT    NOT NULL DEFAULT '',
    created_at   REAL    NOT NULL,
    is_sensitive INTEGER NOT NULL DEFAULT 0,
    tags         TEXT    NOT NULL DEFAULT '',
    full_text    TEXT    NOT NULL DEFAULT '',
    is_starred   INTEGER NOT NULL DEFAULT 0,
    window_title TEXT    NOT NULL DEFAULT '',
    bundle_id    TEXT    NOT NULL DEFAULT '',
    activity     TEXT    NOT NULL DEFAULT '',
    heading      TEXT    NOT NULL DEFAULT '',
    summary      TEXT    NOT NULL DEFAULT '',
    UNIQUE(content_hash)
);

CREATE TABLE IF NOT EXISTS vectors (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id        INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    compressed_data  BLOB    NOT NULL,
    residual_norm    REAL    NOT NULL DEFAULT 0.0,
    encoding_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vault_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id  INTEGER REFERENCES memories(id) ON DELETE CASCADE,
    ciphertext BLOB    NOT NULL,
    nonce      BLOB    NOT NULL,
    created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Tombstones: content hashes the user explicitly deleted. The daemon must
-- refuse to recapture these so a "deleted" memory cannot reappear later
-- when the same clipboard text or window text is observed again.
CREATE TABLE IF NOT EXISTS deleted_hashes (
    content_hash TEXT PRIMARY KEY,
    deleted_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_created_at    ON memories(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_content_hash  ON memories(content_hash);
CREATE INDEX IF NOT EXISTS idx_memories_sensitive      ON memories(is_sensitive);
CREATE INDEX IF NOT EXISTS idx_vectors_memory_id       ON vectors(memory_id);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text_snippet,
    content='memories',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text_snippet) VALUES (new.id, new.text_snippet);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text_snippet) VALUES('delete', old.id, old.text_snippet);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text_snippet) VALUES('delete', old.id, old.text_snippet);
    INSERT INTO memories_fts(rowid, text_snippet) VALUES (new.id, new.text_snippet);
END;
"""

_LIST_COLUMNS = (
    "id, content_hash, text_snippet, source, app_name, created_at, is_sensitive, "
    "tags, is_starred, window_title, bundle_id, activity, heading, summary, "
    "narrative, entities, ai_state"
)
_LIST_COLUMN_NAMES = [
    "id", "content_hash", "text_snippet", "source", "app_name", "created_at",
    "is_sensitive", "tags", "is_starred", "window_title", "bundle_id",
    "activity", "heading", "summary", "narrative", "entities", "ai_state",
]
_LIST_COLUMNS_M = ", ".join(f"m.{name} AS {name}" for name in _LIST_COLUMN_NAMES)


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # PRAGMA foreign_keys is per-connection and MUST be enabled outside
        # any transaction. SQLite silently ignores it inside the schema
        # executescript() call, which is why cascading deletes were not
        # firing (and orphan vectors/vault rows could survive a delete).
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        # Migrations — safe to run repeatedly
        for stmt in [
            "ALTER TABLE memories ADD COLUMN tags      TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN full_text TEXT    NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN is_starred INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE memories ADD COLUMN window_title TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN bundle_id    TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN activity     TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN heading      TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN summary      TEXT NOT NULL DEFAULT ''",
            # AI narrative paragraph (2 to 3 sentence story for the detail panel).
            "ALTER TABLE memories ADD COLUMN narrative    TEXT NOT NULL DEFAULT ''",
            # AI structured facts as JSON: {topic, who, where, gist}.
            "ALTER TABLE memories ADD COLUMN entities     TEXT NOT NULL DEFAULT ''",
            # AI processing state: 'pending' (heuristic only), 'narrated', 'distilled'.
            "ALTER TABLE memories ADD COLUMN ai_state     TEXT NOT NULL DEFAULT 'pending'",
        ]:
            try:
                self._conn.execute(stmt)
            except Exception:
                pass
        self._conn.commit()
        # Upgrade FTS to index full_text (not just the 200-char snippet).
        # Runs once per database, gated by a config flag. Safe to retry.
        self._migrate_fts_v2()

    def _migrate_fts_v2(self) -> None:
        """Rebuild FTS5 to index full_text in addition to text_snippet.

        The original schema only indexed text_snippet (first 200 chars), so
        searches missed anything after the cut-off. This migration drops and
        recreates the FTS table + triggers once, then sets a config marker so
        subsequent launches skip it.
        """
        ver = self._conn.execute(
            "SELECT value FROM config WHERE key = 'fts_schema_version'"
        ).fetchone()
        if ver and ver[0] >= "2":
            return

        try:
            for stmt in [
                "DROP TRIGGER IF EXISTS memories_fts_ai",
                "DROP TRIGGER IF EXISTS memories_fts_ad",
                "DROP TRIGGER IF EXISTS memories_fts_au",
                "DROP TABLE IF EXISTS memories_fts",
                """CREATE VIRTUAL TABLE memories_fts USING fts5(
                    text_snippet, full_text,
                    content='memories', content_rowid='id'
                )""",
                """CREATE TRIGGER memories_fts_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, text_snippet, full_text)
                    VALUES (new.id, new.text_snippet, new.full_text);
                END""",
                """CREATE TRIGGER memories_fts_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, text_snippet, full_text)
                    VALUES('delete', old.id, old.text_snippet, old.full_text);
                END""",
                """CREATE TRIGGER memories_fts_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, text_snippet, full_text)
                    VALUES('delete', old.id, old.text_snippet, old.full_text);
                    INSERT INTO memories_fts(rowid, text_snippet, full_text)
                    VALUES (new.id, new.text_snippet, new.full_text);
                END""",
                "INSERT INTO memories_fts(memories_fts) VALUES('rebuild')",
                "INSERT OR REPLACE INTO config (key, value) VALUES ('fts_schema_version', '2')",
            ]:
                self._conn.execute(stmt)
            self._conn.commit()
            print("[store] FTS upgraded to v2 (full_text indexed)", flush=True)
        except Exception as exc:
            print(f"[store] FTS v2 migration failed: {exc}", flush=True)
            try:
                self._conn.rollback()
            except Exception:
                pass
            # Restore minimal FTS so search still works on old schema.
            try:
                self._conn.execute("DROP TABLE IF EXISTS memories_fts")
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                    "text_snippet, content='memories', content_rowid='id')"
                )
                self._conn.execute(
                    "INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"
                )
                self._conn.commit()
            except Exception:
                pass

    def close(self) -> None:
        self._conn.close()

    # ── Memories ─────────────────────────────────────────────────────────────

    def insert_memory(
        self,
        text: str,
        source: str,
        app_name: str,
        compressed: CompressedVector,
        residual_norm: float,
        dedup_window: float = 300.0,
        tags: str = "",
        window_title: str = "",
        bundle_id: str = "",
        activity: str = "",
        heading: str = "",
        summary: str = "",
    ) -> int | None:
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        cutoff = time.time() - dedup_window

        # Tombstone check: refuse to recapture a hash the user deleted.
        if self.is_hash_deleted(content_hash):
            return None

        existing = self._conn.execute(
            "SELECT id, created_at FROM memories WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing and existing["created_at"] > cutoff:
            return None  # duplicate within window

        snippet = text[:200].replace("\n", " ")
        now = time.time()
        try:
            cur = self._conn.execute(
                "INSERT INTO memories ("
                "content_hash, text_snippet, full_text, source, app_name, created_at, tags, "
                "window_title, bundle_id, activity, heading, summary"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    content_hash, snippet, text, source, app_name, now, tags,
                    window_title, bundle_id, activity, heading, summary,
                ),
            )
            memory_id = cur.lastrowid
            self._conn.execute(
                "INSERT INTO vectors (memory_id, compressed_data, residual_norm) VALUES (?, ?, ?)",
                (memory_id, to_bytes(compressed), residual_norm),
            )
            self._conn.commit()
            return memory_id
        except sqlite3.IntegrityError:
            self._conn.rollback()
            return None

    def insert_sensitive(
        self,
        text: str,
        source: str,
        app_name: str,
        dedup_window: float = 300.0,
    ) -> int | None:
        """Insert a sensitive placeholder in memories. Returns memory_id or None if duplicate."""
        content_hash = hashlib.sha256(text.encode()).hexdigest()
        cutoff = time.time() - dedup_window
        if self.is_hash_deleted(content_hash):
            return None
        existing = self._conn.execute(
            "SELECT id, created_at FROM memories WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing and existing["created_at"] > cutoff:
            return None

        now = time.time()
        try:
            cur = self._conn.execute(
                "INSERT INTO memories (content_hash, text_snippet, source, app_name, created_at, is_sensitive) "
                "VALUES (?, '[SENSITIVE]', ?, ?, ?, 1)",
                (content_hash, source, app_name, now),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            self._conn.rollback()
            return None

    def delete_memory(self, memory_id: int) -> bool:
        """Hard-delete a memory and tombstone its content hash so the daemon
        won't silently re-create it the next time the same text is captured.

        Belt-and-suspenders implementation: even if FK cascades are off (a
        common SQLite gotcha when the pragma was set in the wrong order),
        we explicitly purge the vector + vault rows and the FTS shadow,
        then drop the memory row, then write the tombstone. The whole
        thing runs in a single transaction so partial failures roll back.

        Returns True if a row was deleted.
        """
        try:
            mid = int(memory_id)
        except Exception:
            return False
        row = self._conn.execute(
            "SELECT content_hash, text_snippet FROM memories WHERE id = ?",
            (mid,),
        ).fetchone()
        if not row:
            return False
        chash = row["content_hash"]
        try:
            self._conn.execute("BEGIN")
            # FTS5 contentless-trigger cleanup (defense in depth — the
            # AFTER DELETE trigger should handle this, but we still issue
            # the delete explicitly so a stale FTS row can't survive).
            try:
                self._conn.execute(
                    "INSERT INTO memories_fts(memories_fts, rowid, text_snippet) "
                    "VALUES('delete', ?, ?)",
                    (mid, row["text_snippet"]),
                )
            except Exception:
                pass
            self._conn.execute(
                "DELETE FROM vectors WHERE memory_id = ?", (mid,),
            )
            self._conn.execute(
                "DELETE FROM vault_entries WHERE memory_id = ?", (mid,),
            )
            self._conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
            self._conn.execute(
                "INSERT OR REPLACE INTO deleted_hashes "
                "(content_hash, deleted_at) VALUES (?, ?)",
                (chash, time.time()),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            return False

    def is_hash_deleted(self, content_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM deleted_hashes WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return row is not None

    def forget_deletion(self, content_hash: str) -> None:
        """Lift a tombstone (used if the user explicitly re-allows recapture)."""
        self._conn.execute(
            "DELETE FROM deleted_hashes WHERE content_hash = ?", (content_hash,)
        )
        self._conn.commit()

    def clear_all_memories(self) -> None:
        """Remove every row from memories (vectors cascade). Rebuilds FTS index.

        Also clears tombstones — a full wipe means "start over completely",
        not "permanently block these hashes from ever being captured again".
        """
        try:
            self._conn.execute("DELETE FROM memories")
        except sqlite3.DatabaseError:
            # Corrupted FTS delete triggers can block bulk DELETE; drop them
            # and wipe directly, then rebuild search below.
            for stmt in (
                "DROP TRIGGER IF EXISTS memories_fts_ai",
                "DROP TRIGGER IF EXISTS memories_fts_ad",
                "DROP TRIGGER IF EXISTS memories_fts_au",
            ):
                self._conn.execute(stmt)
            self._conn.execute("DELETE FROM memories")
        self._conn.execute(
            "DELETE FROM vectors WHERE memory_id NOT IN (SELECT id FROM memories)"
        )
        self._conn.execute("DELETE FROM deleted_hashes")
        self._conn.commit()
        try:
            self.rebuild_fts()
        except Exception:
            pass

    def update_ai(
        self,
        memory_id: int,
        *,
        heading: str | None = None,
        summary: str | None = None,
        narrative: str | None = None,
        entities_json: str | None = None,
        ai_state: str | None = None,
    ) -> bool:
        """Update one or more AI-generated fields on an existing memory.

        ``None`` means "leave this field alone". Returns True if at least one
        column changed (so the daemon can log meaningfully).
        """
        sets: list[str] = []
        vals: list = []
        if heading is not None:
            sets.append("heading = ?")
            vals.append(heading)
        if summary is not None:
            sets.append("summary = ?")
            vals.append(summary)
        if narrative is not None:
            sets.append("narrative = ?")
            vals.append(narrative)
        if entities_json is not None:
            sets.append("entities = ?")
            vals.append(entities_json)
        if ai_state is not None:
            sets.append("ai_state = ?")
            vals.append(ai_state)
        if not sets:
            return False
        vals.append(memory_id)
        cur = self._conn.execute(
            f"UPDATE memories SET {', '.join(sets)} "
            "WHERE id = ? AND is_sensitive = 0",
            tuple(vals),
        )
        self._conn.commit()
        return bool(cur.rowcount)

    def get_recent_pending_ai(self, limit: int = 50) -> list[dict]:
        """Return the most recent rows that still need AI processing.

        Used by the daemon at startup to backfill captures that were inserted
        before the AI pipeline existed (or while it was offline). Skips
        sensitive rows entirely."""
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories "
            "WHERE is_sensitive = 0 "
            "AND (ai_state IS NULL OR ai_state != 'distilled') "
            "AND length(coalesce(text_snippet, '')) >= 40 "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_heading_summary(self, memory_id: int, heading: str, summary: str) -> bool:
        """Set AI heading + summary after insert. Returns False if missing or unchanged."""
        row = self._conn.execute(
            "SELECT heading, summary FROM memories WHERE id = ? AND is_sensitive = 0",
            (memory_id,),
        ).fetchone()
        if not row:
            return False
        if (row["heading"] or "") == heading and (row["summary"] or "") == summary:
            return False
        self._conn.execute(
            "UPDATE memories SET heading = ?, summary = ? WHERE id = ?",
            (heading, summary, memory_id),
        )
        self._conn.commit()
        return True

    def toggle_star(self, memory_id: int) -> bool:
        row = self._conn.execute(
            "SELECT is_starred FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if not row: return False
        new_val = 0 if row["is_starred"] else 1
        self._conn.execute("UPDATE memories SET is_starred = ? WHERE id = ?", (new_val, memory_id))
        self._conn.commit()
        return bool(new_val)

    def update_memory_text(self, memory_id: int, new_text: str) -> None:
        from .summaries import memory_title, summarize_subject
        snippet = new_text[:200].replace("\n", " ")
        row = self._conn.execute(
            "SELECT source, app_name, activity, window_title FROM memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
        heading = memory_title(
            row["source"] if row else "manual",
            row["app_name"] if row else "",
            row["activity"] if row else "",
            row["window_title"] if row else "",
            new_text,
        )
        summary = summarize_subject(
            new_text,
            window_title=row["window_title"] if row else "",
            app_name=row["app_name"] if row else "",
            activity=row["activity"] if row else "",
        )
        self._conn.execute(
            "UPDATE memories SET text_snippet = ?, full_text = ?, heading = ?, summary = ? WHERE id = ?",
            (snippet, new_text, heading, summary, memory_id))
        self._conn.commit()

    def update_memory_vector(
        self,
        memory_id: int,
        compressed: CompressedVector,
        residual_norm: float,
    ) -> None:
        """Replace the vector row for an existing memory."""
        self._conn.execute(
            "UPDATE vectors SET compressed_data = ?, residual_norm = ? WHERE memory_id = ?",
            (to_bytes(compressed), float(residual_norm), int(memory_id)),
        )
        self._conn.commit()

    def get_recent_for_activity(
        self,
        *,
        source: str,
        app_name: str,
        window_title: str,
        bundle_id: str = "",
        within_seconds: float = 4 * 3600,
        limit: int = 8,
    ) -> list[dict]:
        """Recent non-sensitive memories for the same capture activity key.

        Activity key is effectively (source + app + bundle + window title),
        which is the same identity used by the daemon's near-repeat dedup.
        """
        cutoff = time.time() - max(0.0, float(within_seconds))
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories "
            "WHERE is_sensitive = 0 "
            "AND lower(source) = lower(?) "
            "AND lower(app_name) = lower(?) "
            "AND lower(bundle_id) = lower(?) "
            "AND lower(window_title) = lower(?) "
            "AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (
                source or "",
                app_name or "",
                bundle_id or "",
                window_title or "",
                float(cutoff),
                int(limit),
            ),
        ).fetchall()
        return [dict(r) for r in rows]

    def bump_memory_timestamp(self, memory_id: int, ts: float | None = None) -> None:
        """Move a memory to the top of timeline after a merged update."""
        when = float(ts or time.time())
        self._conn.execute(
            "UPDATE memories SET created_at = ? WHERE id = ? AND is_sensitive = 0",
            (when, int(memory_id)),
        )
        self._conn.commit()

    def append_memory_update(
        self,
        memory_id: int,
        new_text: str,
        *,
        max_chars: int = 18000,
        update_label: str | None = None,
    ) -> str:
        """Append a new capture chunk to an existing memory as a collective log.

        Returns the merged full_text that was persisted.
        """
        row = self._conn.execute(
            "SELECT full_text FROM memories WHERE id = ? AND is_sensitive = 0",
            (int(memory_id),),
        ).fetchone()
        if not row:
            return new_text
        prev = (row["full_text"] or "").strip()
        chunk = (new_text or "").strip()
        if not chunk:
            return prev
        if not prev:
            merged = chunk
        elif chunk in prev:
            merged = prev
        else:
            label = (update_label or "").strip()
            if not label:
                label = time.strftime("%b %d %H:%M", time.localtime())
            merged = f"{prev}\n\n[Update {label}]\n{chunk}"
        if len(merged) > max_chars:
            merged = merged[-max_chars:]
        self.update_memory_text(int(memory_id), merged)
        return merged

    def get_starred(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories WHERE is_starred = 1 AND is_sensitive = 0 "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_by_date(self, limit: int = 200) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories WHERE is_sensitive = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_memories_in_range(
        self,
        start_ts: float,
        end_ts: float,
        limit: int = 200,
    ) -> list[dict]:
        """Non-sensitive memories whose created_at falls in [start_ts, end_ts).

        Used by the daily digest, time-window queries, and any feature that
        cares about a specific calendar slice of activity.
        """
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories "
            f"WHERE is_sensitive = 0 AND created_at >= ? AND created_at < ? "
            f"ORDER BY created_at ASC LIMIT ?",
            (float(start_ts), float(end_ts), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_vault_entry(self, ciphertext: bytes, nonce: bytes, created_at: float) -> int:
        cur = self._conn.execute(
            "INSERT INTO vault_entries (ciphertext, nonce, created_at) VALUES (?, ?, ?)",
            (ciphertext, nonce, created_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_memory_by_id(self, memory_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT m.*, v.compressed_data, v.residual_norm FROM memories m "
            "LEFT JOIN vectors v ON v.memory_id = m.id WHERE m.id = ?",
            (memory_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_many_by_ids(self, memory_ids: list[int]) -> list[dict]:
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories "
            f"WHERE is_sensitive = 0 AND id IN ({placeholders})",
            tuple(memory_ids),
        ).fetchall()
        by_id = {int(r["id"]): dict(r) for r in rows}
        return [by_id[mid] for mid in memory_ids if mid in by_id]

    def metadata_search(self, query: str, limit: int = 30) -> list[dict]:
        tokens = [
            token for token in re.findall(r"[A-Za-z0-9]{2,}", query.lower())
            if token not in {"the", "and", "for", "with"}
        ][:6]
        if not tokens:
            return []
        fields = [
            "lower(text_snippet)", "lower(heading)", "lower(summary)",
            "lower(app_name)", "lower(window_title)", "lower(activity)", "lower(tags)",
        ]
        clauses = []
        params: list[str | int] = []
        for token in tokens:
            clauses.append("(" + " OR ".join(f"{field} LIKE ?" for field in fields) + ")")
            params.extend([f"%{token}%"] * len(fields))
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories WHERE is_sensitive = 0 AND "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_compressed_vectors(self) -> list[tuple[int, CompressedVector, float]]:
        """Return [(memory_id, CompressedVector, residual_norm), ...] for all non-sensitive memories."""
        rows = self._conn.execute(
            "SELECT m.id, v.compressed_data, v.residual_norm "
            "FROM memories m JOIN vectors v ON v.memory_id = m.id "
            "WHERE m.is_sensitive = 0 ORDER BY m.id"
        ).fetchall()
        return [
            (row["id"], from_bytes(bytes(row["compressed_data"]), float(row["residual_norm"])), float(row["residual_norm"]))
            for row in rows
        ]

    def get_recent(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT {_LIST_COLUMNS} FROM memories WHERE is_sensitive = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_memory_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM memories WHERE is_sensitive = 0").fetchone()[0]

    def fts_search(self, query: str, limit: int = 30) -> list[dict]:
        """Full-text search via FTS5. Returns rows sorted by relevance.

        Each row carries a synthetic ``bm25_score`` field: BM25 from FTS5 is a
        ``negated`` magnitude (lower = better), so we flip the sign and shift
        to keep all values >= 0 for clean fusion downstream. Highest is best.
        """
        if not query.strip():
            return []
        try:
            tokens = [t.strip() for t in query.strip().split() if t.strip()]
            fts_q = " ".join(
                f'"{t.replace(chr(34), "")}"*' for t in tokens
            )
            rows = self._conn.execute(
                f"SELECT {_LIST_COLUMNS_M}, bm25(memories_fts) AS _bm25 "
                "FROM memories m "
                "JOIN memories_fts ON memories_fts.rowid = m.id "
                "WHERE memories_fts MATCH ? AND m.is_sensitive = 0 "
                "ORDER BY _bm25 ASC LIMIT ?",
                (fts_q, limit),
            ).fetchall()
            out: list[dict] = []
            for r in rows:
                d = dict(r)
                bm = d.pop("_bm25", None)
                d["bm25_score"] = (-float(bm)) if bm is not None else 0.0
                out.append(d)
            return out
        except sqlite3.OperationalError:
            return []

    def rebuild_fts(self) -> None:
        """Rebuild FTS index from scratch (run after schema migration)."""
        self._conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        self._conn.commit()

    def compact(self) -> dict:
        """Reclaim free pages and refresh planner stats.

        Runs ``VACUUM`` (which rebuilds the database file dropping any
        free pages from tombstoned rows), then ``ANALYZE`` (refreshes
        sqlite_stat1 so the query planner picks correct indexes after
        large deletes), then an FTS5 'optimize' for the search index.
        Returns a small dict with the before/after byte sizes."""
        import os as _os
        # VACUUM cannot run inside an explicit transaction.
        try:
            self._conn.commit()
        except Exception:
            pass
        db_path = None
        try:
            row = self._conn.execute("PRAGMA database_list").fetchone()
            if row and len(row) >= 3:
                db_path = row[2]
        except Exception:
            db_path = None
        before = (_os.path.getsize(db_path) if db_path and _os.path.exists(db_path) else 0)
        try:
            self._conn.execute("VACUUM")
        except Exception:
            pass
        try:
            self._conn.execute("ANALYZE")
        except Exception:
            pass
        try:
            self._conn.execute(
                "INSERT INTO memories_fts(memories_fts) VALUES('optimize')"
            )
            self._conn.commit()
        except Exception:
            pass
        after = (_os.path.getsize(db_path) if db_path and _os.path.exists(db_path) else 0)
        return {
            "db_path": db_path or "",
            "bytes_before": int(before),
            "bytes_after": int(after),
            "bytes_reclaimed": int(max(0, before - after)),
        }

    # ── Vault ─────────────────────────────────────────────────────────────────

    def get_vault_entries(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, memory_id, created_at FROM vault_entries ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_vault_ciphertext(self, vault_id: int) -> tuple[bytes, bytes]:
        row = self._conn.execute(
            "SELECT ciphertext, nonce FROM vault_entries WHERE id = ?", (vault_id,)
        ).fetchone()
        if not row:
            raise KeyError(f"Vault entry {vault_id} not found")
        return bytes(row["ciphertext"]), bytes(row["nonce"])

    # ── Config ────────────────────────────────────────────────────────────────

    def set_config(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_config(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
