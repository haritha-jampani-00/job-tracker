"""Supabase (PostgreSQL) database backend.

Drop-in replacement for JobDB (SQLite). Uses the same interface so
the pipeline and UI work without changes.

Setup:
  1. Create a Supabase project at https://supabase.com (free tier)
  2. Run the SQL in supabase_schema.sql via the SQL Editor
  3. Set SUPABASE_URL and SUPABASE_KEY in .env
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

from src.models import Job, PROTECTED_STATES

logger = logging.getLogger(__name__)


def _get_client():
    from supabase import create_client

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be set in .env. "
            "Get them from your Supabase project Settings > API."
        )
    return create_client(url, key)


class SupabaseJobDB:
    """Supabase-backed job database with the same interface as JobDB."""

    def __init__(self) -> None:
        self.client = _get_client()

    # ── Writes ──────────────────────────────────────────────

    def insert_job(self, job: Job) -> bool:
        """Insert a job. Returns True if inserted, False if duplicate."""
        data = {
            "source": job.source,
            "company": job.company,
            "title": job.title,
            "location": job.location,
            "department": job.department,
            "employment_type": job.employment_type,
            "url": job.url,
            "date_posted": job.date_posted.isoformat() if job.date_posted else None,
            "date_found": job.date_found.isoformat(),
            "description_snippet": job.description_snippet,
            "description_full": job.description_full,
            "match_score": job.match_score,
            "recommendation": job.recommendation,
            "strong_matches": job.strong_matches,
            "missing_keywords": job.missing_keywords,
            "red_flags": job.red_flags,
            "experience_alignment": job.experience_alignment,
            "resume_improvement_prompt": job.resume_improvement_prompt,
            "sponsorship_status": job.sponsorship_status,
            "experience_level": job.experience_level,
            "status": job.status,
            "notes": job.notes,
            "roles_matched": job.roles_matched,
            "canonical_key": job.canonical_key(),
            "is_visible": job.is_visible,
            "filter_reason": job.filter_reason,
            "job_status": job.job_status,
            "applied_at": job.applied_at.isoformat() if job.applied_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
        try:
            self.client.table("jobs").insert(data).execute()
            return True
        except Exception as e:
            err = str(e).lower()
            if "duplicate" in err or "unique" in err:
                return False
            # Column may not exist yet — retry without new columns
            for col in ("description_full", "resume_improvement_prompt", "sponsorship_status", "experience_level"):
                data.pop(col, None)
            try:
                self.client.table("jobs").insert(data).execute()
                return True
            except Exception as e2:
                if "duplicate" in str(e2).lower() or "unique" in str(e2).lower():
                    return False
                logger.error(f"Supabase insert failed: {e2}")
                return False

    def update_roles_matched(self, canonical_key: str, new_roles: list[str]) -> None:
        """Merge additional matched roles into an existing job."""
        resp = (
            self.client.table("jobs")
            .select("roles_matched")
            .eq("canonical_key", canonical_key)
            .execute()
        )
        if not resp.data:
            return
        existing = resp.data[0].get("roles_matched", [])
        if isinstance(existing, str):
            existing = json.loads(existing)
        merged = sorted(set(existing) | set(new_roles))
        (
            self.client.table("jobs")
            .update({"roles_matched": merged})
            .eq("canonical_key", canonical_key)
            .execute()
        )

    def log_run(self, roles: list[str], jobs_found: int, jobs_added: int) -> None:
        self.client.table("run_log").insert({
            "run_time": datetime.utcnow().isoformat(),
            "roles": roles,
            "jobs_found": jobs_found,
            "jobs_added": jobs_added,
        }).execute()

    def update_status(self, job_id: int, status: str, notes: str = "") -> None:
        (
            self.client.table("jobs")
            .update({"status": status, "notes": notes})
            .eq("id", job_id)
            .execute()
        )

    def update_job_status(self, job_id: int, new_status: str) -> None:
        """Update the lifecycle status of a job.

        - Sets updated_at on every change.
        - Sets applied_at on the first transition to "Applied".
        - Protected states force is_visible=True.
        """
        now = datetime.now().isoformat()

        resp = (
            self.client.table("jobs")
            .select("job_status, applied_at")
            .eq("id", job_id)
            .execute()
        )
        if not resp.data:
            return

        applied_at = resp.data[0].get("applied_at")
        if new_status == "Applied" and not applied_at:
            applied_at = now

        updates: dict = {
            "job_status": new_status,
            "updated_at": now,
            "applied_at": applied_at,
        }
        if new_status in PROTECTED_STATES:
            updates["is_visible"] = True
            updates["filter_reason"] = ""

        (
            self.client.table("jobs")
            .update(updates)
            .eq("id", job_id)
            .execute()
        )

    def count_applications_today(self) -> int:
        """Count jobs marked 'Applied' today (local time)."""
        today = datetime.now().strftime("%Y-%m-%d")
        resp = (
            self.client.table("jobs")
            .select("id")
            .gte("applied_at", f"{today}T00:00:00")
            .lt("applied_at", f"{today}T23:59:59")
            .execute()
        )
        return len(resp.data)

    def auto_archive_stale(self, days: int = 30) -> int:
        """Archive Discovered jobs older than `days` days."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        now = datetime.utcnow().isoformat()

        resp = (
            self.client.table("jobs")
            .update({
                "job_status": "Archived",
                "updated_at": now,
                "is_visible": False,
                "filter_reason": f"Auto-archived (stale > {days} days)",
            })
            .eq("job_status", "Discovered")
            .lt("date_found", cutoff)
            .execute()
        )
        count = len(resp.data) if resp.data else 0
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
        payload = {
            "match_score": match_score,
            "recommendation": recommendation,
            "strong_matches": strong_matches,
            "missing_keywords": missing_keywords,
            "red_flags": red_flags,
            "experience_alignment": experience_alignment,
            "resume_improvement_prompt": resume_improvement_prompt,
            "sponsorship_status": sponsorship_status,
            "experience_level": experience_level,
            "is_visible": is_visible,
            "filter_reason": filter_reason,
        }
        try:
            self.client.table("jobs").update(payload).eq("id", job_id).execute()
        except Exception:
            # Fallback: columns may not exist yet — retry without them
            payload.pop("resume_improvement_prompt", None)
            payload.pop("sponsorship_status", None)
            payload.pop("experience_level", None)
            self.client.table("jobs").update(payload).eq("id", job_id).execute()

    def update_job_visibility(self, job_id: int, is_visible: bool, filter_reason: str) -> None:
        """Lightweight update: only is_visible and filter_reason (used by apply_filters_only)."""
        (
            self.client.table("jobs")
            .update({"is_visible": is_visible, "filter_reason": filter_reason})
            .eq("id", job_id)
            .execute()
        )

    def get_all_jobs_raw(self) -> list[dict]:
        """Return all jobs without filtering (for recompute)."""
        resp = self.client.table("jobs").select("*").order("id").execute()
        rows = resp.data or []
        for row in rows:
            for fld in ("strong_matches", "missing_keywords", "red_flags", "roles_matched"):
                val = row.get(fld)
                if isinstance(val, list):
                    row[fld] = json.dumps(val)
        return rows

    # ── Reads ───────────────────────────────────────────────

    def get_all_jobs(
        self,
        role_filter: str | None = None,
        status_filter: list[str] | None = None,
    ) -> list[dict]:
        query = self.client.table("jobs").select("*")
        if role_filter:
            query = query.filter("roles_matched", "cs", json.dumps([role_filter]))
        if status_filter:
            query = query.in_("job_status", status_filter)
        resp = query.order("match_score", desc=True).order("date_found", desc=True).execute()
        rows = resp.data or []
        # Normalize JSON fields to strings for compatibility with UI
        for row in rows:
            for fld in ("strong_matches", "missing_keywords", "red_flags", "roles_matched"):
                val = row.get(fld)
                if isinstance(val, list):
                    row[fld] = json.dumps(val)
        return rows

    def get_existing_keys(self) -> set[str]:
        resp = self.client.table("jobs").select("canonical_key").execute()
        return {r["canonical_key"] for r in (resp.data or [])}

    def get_last_run(self) -> dict | None:
        resp = (
            self.client.table("run_log")
            .select("*")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def url_exists(self, url: str) -> bool:
        resp = self.client.table("jobs").select("id").eq("url", url).limit(1).execute()
        return bool(resp.data)

    # ── Discovered slugs ─────────────────────────────────────

    def get_discovered_slugs(self) -> dict[str, list[str]]:
        """Return active discovered slugs grouped by ATS type."""
        result: dict[str, list[str]] = {"greenhouse": [], "lever": [], "ashby": []}
        try:
            resp = (
                self.client.table("discovered_slugs")
                .select("slug, ats_type")
                .eq("is_active", True)
                .execute()
            )
            for row in resp.data or []:
                ats = row["ats_type"]
                if ats in result:
                    result[ats].append(row["slug"])
        except Exception as e:
            logger.warning(f"Supabase get_discovered_slugs failed (table may not exist): {e}")
        return result

    def get_known_slugs(self) -> set[str]:
        """Return all slug strings from discovered_slugs (active or not)."""
        try:
            resp = self.client.table("discovered_slugs").select("slug").execute()
            return {row["slug"] for row in (resp.data or [])}
        except Exception:
            return set()

    def save_discovered_slugs(
        self,
        discovered: dict[str, list[tuple[str, str]]],
    ) -> int:
        """Upsert discovered slugs into Supabase.

        Args:
            discovered: {"greenhouse": [(slug, company_name), ...], ...}

        Returns:
            Number of new slugs saved.
        """
        now = datetime.utcnow().isoformat()
        saved = 0
        for ats_type, entries in discovered.items():
            for slug, company_name in entries:
                data = {
                    "slug": slug,
                    "ats_type": ats_type,
                    "company_name": company_name,
                    "discovered_at": now,
                    "last_verified": now,
                    "is_active": True,
                }
                try:
                    self.client.table("discovered_slugs").upsert(
                        data, on_conflict="slug,ats_type"
                    ).execute()
                    saved += 1
                except Exception as e:
                    logger.warning(f"Supabase save slug {slug} failed: {e}")
        return saved

    def close(self) -> None:
        pass  # No persistent connection to close
