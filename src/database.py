"""SQLite database operations."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from src.models import Job, PROTECTED_STATES

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,
    company             TEXT NOT NULL,
    title               TEXT NOT NULL,
    location            TEXT DEFAULT '',
    department          TEXT DEFAULT '',
    employment_type     TEXT DEFAULT '',
    url                 TEXT NOT NULL,
    date_posted         TEXT,
    date_found          TEXT NOT NULL,
    description_snippet TEXT DEFAULT '',
    description_full    TEXT DEFAULT '',
    match_score         INTEGER DEFAULT 0,
    recommendation      TEXT DEFAULT '',
    strong_matches      TEXT DEFAULT '[]',
    missing_keywords    TEXT DEFAULT '[]',
    red_flags           TEXT DEFAULT '[]',
    experience_alignment TEXT DEFAULT '',
    resume_improvement_prompt TEXT DEFAULT '',
    sponsorship_status  TEXT DEFAULT 'unknown',
    experience_level    TEXT DEFAULT '',
    status              TEXT DEFAULT 'Not Applied',
    notes               TEXT DEFAULT '',
    roles_matched       TEXT DEFAULT '[]',
    canonical_key       TEXT NOT NULL,
    is_visible          INTEGER DEFAULT 1,
    filter_reason       TEXT DEFAULT '',
    job_status          TEXT DEFAULT 'Discovered',
    applied_at          TEXT,
    updated_at          TEXT,
    UNIQUE(canonical_key)
);

CREATE TABLE IF NOT EXISTS run_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time    TEXT NOT NULL,
    roles       TEXT NOT NULL,
    jobs_found  INTEGER DEFAULT 0,
    jobs_added  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS discovered_slugs (
    slug            TEXT NOT NULL,
    ats_type        TEXT NOT NULL,
    company_name    TEXT DEFAULT '',
    discovered_at   TEXT NOT NULL,
    last_verified   TEXT,
    is_active       INTEGER DEFAULT 1,
    PRIMARY KEY (slug, ats_type)
);
"""


class JobDB:
    """Thin wrapper around SQLite for job storage."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns that may be missing from older databases."""
        cursor = self.conn.execute("PRAGMA table_info(jobs)")
        existing = {row[1] for row in cursor.fetchall()}
        migrations = [
            ("is_visible", "INTEGER DEFAULT 1"),
            ("filter_reason", "TEXT DEFAULT ''"),
            ("job_status", "TEXT DEFAULT 'Discovered'"),
            ("applied_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("resume_improvement_prompt", "TEXT DEFAULT ''"),
            ("description_full", "TEXT DEFAULT ''"),
            ("sponsorship_status", "TEXT DEFAULT 'unknown'"),
            ("experience_level", "TEXT DEFAULT ''"),
        ]
        for col, typedef in migrations:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
                logger.info(f"Migrated: added column '{col}' to jobs table")

        # Data migration: map old `status` values to new `job_status`
        if "job_status" not in existing:
            self.conn.execute("""
                UPDATE jobs SET job_status = CASE
                    WHEN status IN ('Applied') THEN 'Applied'
                    WHEN status IN ('Interview', 'Phone Screen', 'Technical', 'Onsite') THEN 'Interviewing'
                    WHEN status = 'Offer' THEN 'Offer'
                    WHEN status = 'Rejected' THEN 'Rejected'
                    WHEN status = 'Withdrawn' THEN 'Withdrawn'
                    ELSE 'Discovered'
                END
            """)
            logger.info("Migrated: mapped old status values to job_status")

        self.conn.commit()

    # ── Writes ──────────────────────────────────────────────

    def insert_job(self, job: Job) -> bool:
        """Insert a job. Returns True if inserted, False if duplicate."""
        try:
            self.conn.execute(
                """INSERT INTO jobs
                   (source, company, title, location, department, employment_type,
                    url, date_posted, date_found, description_snippet, description_full,
                    match_score, recommendation, strong_matches, missing_keywords,
                    red_flags, experience_alignment, resume_improvement_prompt,
                    sponsorship_status, experience_level,
                    status, notes, roles_matched,
                    canonical_key, is_visible, filter_reason,
                    job_status, applied_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.source,
                    job.company,
                    job.title,
                    job.location,
                    job.department,
                    job.employment_type,
                    job.url,
                    job.date_posted.isoformat() if job.date_posted else None,
                    job.date_found.isoformat(),
                    job.description_snippet,
                    job.description_full,
                    job.match_score,
                    job.recommendation,
                    json.dumps(job.strong_matches),
                    json.dumps(job.missing_keywords),
                    json.dumps(job.red_flags),
                    job.experience_alignment,
                    job.resume_improvement_prompt,
                    job.sponsorship_status,
                    job.experience_level,
                    job.status,
                    job.notes,
                    json.dumps(job.roles_matched),
                    job.canonical_key(),
                    1 if job.is_visible else 0,
                    job.filter_reason,
                    job.job_status,
                    job.applied_at.isoformat() if job.applied_at else None,
                    job.updated_at.isoformat() if job.updated_at else None,
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_roles_matched(self, canonical_key: str, new_roles: list[str]) -> None:
        """Merge additional matched roles into an existing job."""
        row = self.conn.execute(
            "SELECT roles_matched FROM jobs WHERE canonical_key = ?", (canonical_key,)
        ).fetchone()
        if not row:
            return
        existing = json.loads(row["roles_matched"]) if row["roles_matched"] else []
        merged = sorted(set(existing) | set(new_roles))
        self.conn.execute(
            "UPDATE jobs SET roles_matched = ? WHERE canonical_key = ?",
            (json.dumps(merged), canonical_key),
        )
        self.conn.commit()

    def log_run(self, roles: list[str], jobs_found: int, jobs_added: int) -> None:
        self.conn.execute(
            "INSERT INTO run_log (run_time, roles, jobs_found, jobs_added) VALUES (?,?,?,?)",
            (datetime.utcnow().isoformat(), json.dumps(roles), jobs_found, jobs_added),
        )
        self.conn.commit()

    # ── Reads ───────────────────────────────────────────────

    def get_all_jobs(
        self,
        role_filter: str | None = None,
        status_filter: list[str] | None = None,
    ) -> list[dict]:
        """Return jobs, optionally filtered by role and/or lifecycle status."""
        conditions = []
        params: list = []

        if role_filter:
            conditions.append("roles_matched LIKE ?")
            params.append(f"%{role_filter}%")

        if status_filter:
            placeholders = ",".join("?" for _ in status_filter)
            conditions.append(f"job_status IN ({placeholders})")
            params.extend(status_filter)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM jobs{where} ORDER BY match_score DESC, date_found DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_existing_keys(self) -> set[str]:
        """Return all canonical keys currently in the DB."""
        rows = self.conn.execute("SELECT canonical_key FROM jobs").fetchall()
        return {r["canonical_key"] for r in rows}

    def get_last_run(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def url_exists(self, url: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone()
        return row is not None

    def update_status(self, job_id: int, status: str, notes: str = "") -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, notes = ? WHERE id = ?",
            (status, notes, job_id),
        )
        self.conn.commit()

    def update_job_status(self, job_id: int, new_status: str) -> None:
        """Update the lifecycle status of a job.

        - Sets updated_at on every change.
        - Sets applied_at on the first transition to "Applied".
        - Protected states (Applied/Interviewing/Offer) force is_visible=True.
        """
        now = datetime.now().isoformat()

        # Check if this is the first "Applied" transition
        row = self.conn.execute(
            "SELECT job_status, applied_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            return

        applied_at = row["applied_at"]
        if new_status == "Applied" and not applied_at:
            applied_at = now

        # Protected states are always visible
        updates = {
            "job_status": new_status,
            "updated_at": now,
            "applied_at": applied_at,
        }
        if new_status in PROTECTED_STATES:
            updates["is_visible"] = 1
            updates["filter_reason"] = ""

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE id = ?",
            (*updates.values(), job_id),
        )
        self.conn.commit()

    def count_applications_today(self) -> int:
        """Count jobs marked 'Applied' today (local time)."""
        today = datetime.now().strftime("%Y-%m-%d")
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE DATE(applied_at) = ?",
            (today,),
        ).fetchone()
        return row["cnt"] if row else 0

    def auto_archive_stale(self, days: int = 30) -> int:
        """Archive Discovered jobs older than `days` days.

        Returns the number of jobs archived.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        now = datetime.utcnow().isoformat()
        reason = f"Auto-archived (stale > {days} days)"
        cursor = self.conn.execute(
            """UPDATE jobs
               SET job_status = 'Archived', updated_at = ?, is_visible = 0,
                   filter_reason = ?
               WHERE job_status = 'Discovered' AND date_found < ?""",
            (now, reason, cutoff),
        )
        count = cursor.rowcount
        self.conn.commit()
        if count:
            logger.info(f"Auto-archived {count} stale Discovered jobs (>{days} days)")
        return count

    def update_job_scoring(self, job_id: int, match_score: int, recommendation: str,
                           strong_matches: list, missing_keywords: list, red_flags: list,
                           experience_alignment: str, is_visible: bool, filter_reason: str,
                           resume_improvement_prompt: str = "",
                           sponsorship_status: str = "unknown",
                           experience_level: str = "") -> None:
        """Update scoring/visibility fields for a single job (used by recompute)."""
        self.conn.execute(
            """UPDATE jobs SET match_score=?, recommendation=?, strong_matches=?,
               missing_keywords=?, red_flags=?, experience_alignment=?,
               resume_improvement_prompt=?, sponsorship_status=?, experience_level=?,
               is_visible=?, filter_reason=? WHERE id=?""",
            (match_score, recommendation, json.dumps(strong_matches),
             json.dumps(missing_keywords), json.dumps(red_flags),
             experience_alignment, resume_improvement_prompt, sponsorship_status,
             experience_level,
             1 if is_visible else 0, filter_reason, job_id),
        )
        self.conn.commit()

    def update_job_visibility(self, job_id: int, is_visible: bool, filter_reason: str) -> None:
        """Lightweight update: only is_visible and filter_reason (used by apply_filters_only)."""
        self.conn.execute(
            "UPDATE jobs SET is_visible=?, filter_reason=? WHERE id=?",
            (1 if is_visible else 0, filter_reason, job_id),
        )
        self.conn.commit()

    def get_all_jobs_raw(self) -> list[dict]:
        """Return all jobs without filtering (for recompute)."""
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Discovered slugs ─────────────────────────────────────

    def get_discovered_slugs(self) -> dict[str, list[str]]:
        """Return active discovered slugs grouped by ATS type."""
        result: dict[str, list[str]] = {"greenhouse": [], "lever": [], "ashby": []}
        rows = self.conn.execute(
            "SELECT slug, ats_type FROM discovered_slugs WHERE is_active = 1"
        ).fetchall()
        for row in rows:
            ats = row["ats_type"]
            if ats in result:
                result[ats].append(row["slug"])
        return result

    def get_known_slugs(self) -> set[str]:
        """Return all slug strings from discovered_slugs (active or not)."""
        rows = self.conn.execute("SELECT slug FROM discovered_slugs").fetchall()
        return {row["slug"] for row in rows}

    def save_discovered_slugs(
        self,
        discovered: dict[str, list[tuple[str, str]]],
    ) -> int:
        """Upsert discovered slugs into the DB.

        Args:
            discovered: {"greenhouse": [(slug, company_name), ...], ...}

        Returns:
            Number of new slugs saved.
        """
        now = datetime.utcnow().isoformat()
        saved = 0
        for ats_type, entries in discovered.items():
            for slug, company_name in entries:
                try:
                    self.conn.execute(
                        """INSERT INTO discovered_slugs
                           (slug, ats_type, company_name, discovered_at, last_verified, is_active)
                           VALUES (?, ?, ?, ?, ?, 1)""",
                        (slug, ats_type, company_name, now, now),
                    )
                    saved += 1
                except sqlite3.IntegrityError:
                    # Already exists — update last_verified
                    self.conn.execute(
                        """UPDATE discovered_slugs
                           SET last_verified = ?, is_active = 1
                           WHERE slug = ? AND ats_type = ?""",
                        (now, slug, ats_type),
                    )
        self.conn.commit()
        return saved

    def close(self) -> None:
        self.conn.close()
