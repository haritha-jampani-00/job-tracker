"""Main pipeline: fetch → filter → deduplicate → score → store → export.

Supports multiple roles in a single run. Jobs are fetched once, then filtered
against ALL roles simultaneously. A single job can match multiple roles.
"""

from __future__ import annotations

import json as _json
import logging

from src.config import AppConfig, SearchConfig, get_db
from src.dedup import deduplicate
from src.exporter import export_to_excel
from src.fetchers import AshbyFetcher, GreenhouseFetcher, LeverFetcher
from src.fetchers.startup import PORTAL_FETCHERS
from src.slug_discovery import discover_new_slugs, fetch_yc_company_names
from src.filters import (
    apply_experience_gate,
    apply_sponsorship_filter,
    cap_results,
    detect_experience_level,
    detect_sponsorship_status,
    filter_by_freshness,
    filter_by_location,
    filter_by_relevance,
    filter_by_score,
)
from src.models import Job, PROTECTED_STATES, TERMINAL_STATES
from src.relevance import extract_resume_keywords
from src.resume_loader import load_all_resumes
from src.scoring.llm_scoring import LLMScorer
from src.scoring.rule_based import RuleBasedScorer

logger = logging.getLogger(__name__)


def _source_counts(jobs: list[Job]) -> dict[str, int]:
    """Count jobs grouped by source."""
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.source] = counts.get(job.source, 0) + 1
    return counts


def _log_source_counts(jobs: list[Job], stage: str) -> None:
    """Log job counts grouped by source for pipeline debugging."""
    counts = _source_counts(jobs)
    parts = [f"{source}: {count}" for source, count in sorted(counts.items())]
    logger.info(f"[{stage}] {' | '.join(parts)} (total: {len(jobs)})")


def _fetch_all(cfg: AppConfig, db=None) -> list[Job]:
    """Fetch jobs from all configured boards and startup portals.

    Merges hardcoded config slugs with auto-discovered slugs from the DB.
    """
    all_jobs: list[Job] = []

    # ── Company boards ─────────────────────────────────────
    gh = list(cfg.companies.get("greenhouse", []))
    lv = list(cfg.companies.get("lever", []))
    ab = list(cfg.companies.get("ashby", []))

    # Merge discovered slugs from DB
    if db is not None:
        try:
            discovered = db.get_discovered_slugs()
            gh = list(set(gh) | set(discovered.get("greenhouse", [])))
            lv = list(set(lv) | set(discovered.get("lever", [])))
            ab = list(set(ab) | set(discovered.get("ashby", [])))
            extra = (len(discovered.get("greenhouse", [])) +
                     len(discovered.get("lever", [])) +
                     len(discovered.get("ashby", [])))
            if extra:
                logger.info(f"Merged {extra} discovered slugs into fetch list")
        except Exception as e:
            logger.warning(f"Could not load discovered slugs: {e}")

    if gh:
        all_jobs.extend(GreenhouseFetcher().fetch_many(gh))
    if lv:
        all_jobs.extend(LeverFetcher().fetch_many(lv))
    if ab:
        all_jobs.extend(AshbyFetcher().fetch_many(ab))

    # ── Startup portals ────────────────────────────────────
    portals = getattr(cfg, "startup_portals", []) or []
    for portal_name in portals:
        fetcher_cls = PORTAL_FETCHERS.get(portal_name.lower())
        if fetcher_cls:
            try:
                fetcher = fetcher_cls()
                jobs = fetcher.fetch_many([])
                logger.info(f"[{fetcher.source_name}] {len(jobs)} jobs")
                all_jobs.extend(jobs)
            except Exception as e:
                logger.warning(f"[{portal_name}] portal fetch failed – {e}")
        else:
            logger.warning(f"Unknown portal: {portal_name}")

    return all_jobs


def run_pipeline(
    cfg: AppConfig,
    search_override: SearchConfig | None = None,
    require_sponsorship: bool = True,
    allowed_experience_levels: list[str] | None = None,
) -> dict:
    """Execute a full multi-role fetch-score-filter-store cycle.

    Jobs are fetched once, then filtered by relevance against ALL roles.
    Each job is tagged with whichever roles matched its title.
    Deduplication merges roles when the same job matches multiple queries.

    Returns:
        dict with keys: jobs_found, jobs_added, jobs_exported, roles.
    """
    search = search_override or cfg.search
    db = get_db(cfg)

    try:
        roles = search.roles
        logger.info(f"Fetching jobs for roles: {roles}")

        # Per-source debug tracking
        debug: dict[str, dict[str, int]] = {}

        # ── 1. Fetch (once for all roles) ────────────────────
        all_jobs = _fetch_all(cfg, db=db)
        logger.info(f"Total raw jobs fetched: {len(all_jobs)}")
        _log_source_counts(all_jobs, "Fetched")
        debug["fetched"] = _source_counts(all_jobs)

        # ── 2. Load resumes & extract keywords (early) ──────
        #    Needed by the relevance filter for resume-aware scoring.
        resume_texts = load_all_resumes(
            cfg.resumes_dir, roles, fallback_path=cfg.resume_path,
        )
        loaded_roles = list(resume_texts.keys())
        missing_roles = [r for r in roles if r not in resume_texts]
        if missing_roles:
            logger.warning(f"No resume for roles: {missing_roles} — scoring skipped for these")
        logger.info(f"Resumes loaded for: {loaded_roles}")

        # Merge all resume texts and extract keywords for relevance filter
        combined_resume = " ".join(resume_texts.values())
        resume_keywords = extract_resume_keywords(combined_resume) if combined_resume.strip() else set()
        logger.info(f"Extracted {len(resume_keywords)} keywords from resumes")

        # ── 3. Resume-aware relevance filter ─────────────────
        #    Soft scoring: keyword overlap (60%) + title similarity (20%)
        #    + role boost (20%).  Keeps jobs with score >= 35.
        all_jobs = filter_by_relevance(all_jobs, search, resume_keywords=resume_keywords)
        logger.info(f"After relevance filter: {len(all_jobs)}")
        _log_source_counts(all_jobs, "After relevance")
        debug["relevant"] = _source_counts(all_jobs)

        # ── 4. Location filter ───────────────────────────────
        all_jobs = filter_by_location(all_jobs, search)
        _log_source_counts(all_jobs, "After location")

        # ── 5. Freshness filter ──────────────────────────────
        #    Uses date_found (not date_posted) so Lever/Ashby jobs
        #    aren't incorrectly discarded for having old posting dates.
        all_jobs = filter_by_freshness(all_jobs, cfg.freshness_days)
        logger.info(f"After freshness filter: {len(all_jobs)}")
        _log_source_counts(all_jobs, "After freshness")
        debug["fresh"] = _source_counts(all_jobs)

        # ── 6. Deduplicate (cross-role merging) ──────────────
        existing_keys = db.get_existing_keys()
        all_jobs = deduplicate(all_jobs, existing_keys)
        logger.info(f"After dedup: {len(all_jobs)} new jobs")
        _log_source_counts(all_jobs, "After dedup")

        jobs_found = len(all_jobs)

        # ── 7. Score ─────────────────────────────────────────
        if cfg.llm_enabled:
            scorer = LLMScorer(resume_texts, search)
        else:
            scorer = RuleBasedScorer(resume_texts, search)

        # Only score jobs that have at least one role with a resume
        scorable = [j for j in all_jobs if any(r in resume_texts for r in j.roles_matched)]
        skipped = len(all_jobs) - len(scorable)
        if skipped:
            logger.info(f"Skipping {skipped} jobs (no resume for their matched roles)")

        all_jobs = scorer.score_batch(scorable)

        # ── 8. Experience level gate ──────────────────────────
        #    Detect experience level from title and filter by
        #    user's allowed levels preference.
        all_jobs = apply_experience_gate(all_jobs, allowed_levels=allowed_experience_levels)

        # ── 8b. Sponsorship filter ────────────────────────
        all_jobs = apply_sponsorship_filter(all_jobs, require_sponsorship)

        # ── 9. Filter by score ───────────────────────────────
        all_jobs = filter_by_score(all_jobs, cfg.score_threshold)
        logger.info(f"After score filter (>={cfg.score_threshold}): {len(all_jobs)}")

        # ── 10. Cap results ──────────────────────────────────
        all_jobs = cap_results(all_jobs, cfg.max_results_per_run)
        _log_source_counts(all_jobs, "Final (stored)")
        debug["stored"] = _source_counts(all_jobs)

        # ── 11. Store ────────────────────────────────────────
        added = 0
        for job in all_jobs:
            if db.insert_job(job):
                added += 1
            else:
                # Job exists — merge any new roles
                db.update_roles_matched(job.canonical_key(), job.roles_matched)

        db.log_run(roles, jobs_found, added)
        logger.info(f"Stored {added} new jobs in DB")

        # ── 12. Auto-archive stale Discovered jobs ───────────
        archived = db.auto_archive_stale(days=30)
        if archived:
            logger.info(f"Auto-archived {archived} stale jobs")

        # ── 13. Export ───────────────────────────────────────
        all_db_jobs = db.get_all_jobs()
        export_to_excel(all_db_jobs, cfg.excel_path)

        return {
            "jobs_found": jobs_found,
            "jobs_added": added,
            "jobs_exported": len(all_db_jobs),
            "roles": roles,
            "debug": debug,
        }

    finally:
        db.close()


def recompute_all(
    cfg: AppConfig,
    search_override: SearchConfig | None = None,
    require_sponsorship: bool = True,
    allowed_experience_levels: list[str] | None = None,
) -> dict:
    """Re-score and re-filter ALL existing jobs in the DB.

    Called when the user changes settings (roles, years, keywords, threshold).
    Reads every job from the DB, re-runs scoring and the experience gate,
    and updates is_visible / filter_reason / match_score / recommendation.

    Returns:
        dict with keys: total, visible, hidden.
    """
    search = search_override or cfg.search
    db = get_db(cfg)

    try:
        all_rows = db.get_all_jobs_raw()
        if not all_rows:
            return {"total": 0, "visible": 0, "hidden": 0}

        logger.info(f"Recompute: processing {len(all_rows)} jobs")

        # Load resumes
        resume_texts = load_all_resumes(
            cfg.resumes_dir, search.roles, fallback_path=cfg.resume_path,
        )

        # Build scorer
        if cfg.llm_enabled:
            scorer = LLMScorer(resume_texts, search)
        else:
            scorer = RuleBasedScorer(resume_texts, search)

        visible_count = 0
        hidden_count = 0

        for row in all_rows:
            job_status = row.get("job_status", "Discovered")

            # Reconstruct a Job for scoring (with full description if available)
            job = Job(
                source=row["source"],
                company=row["company"],
                title=row["title"],
                location=row["location"],
                url=row["url"],
                department=row.get("department", ""),
                employment_type=row.get("employment_type", ""),
                description_snippet=row.get("description_snippet", ""),
                description_full=row.get("description_full", ""),
            )
            # Restore roles_matched from DB
            rm = row.get("roles_matched", "[]")
            if isinstance(rm, str):
                job.roles_matched = _json.loads(rm) if rm else []
            else:
                job.roles_matched = rm if rm else []

            # Always re-score — scorer falls back to all resume tokens
            # if no role-specific match exists
            if resume_texts:
                scorer.score(job)
            else:
                # No matching resume — preserve ALL existing scoring from DB
                job.match_score = row.get("match_score", 0)
                job.recommendation = row.get("recommendation", "")
                job.experience_alignment = row.get("experience_alignment", "")
                job.resume_improvement_prompt = row.get("resume_improvement_prompt", "")
                # Restore list fields from DB (may be JSON strings or lists)
                for attr in ("strong_matches", "missing_keywords", "red_flags"):
                    val = row.get(attr, "[]")
                    if isinstance(val, str):
                        setattr(job, attr, _json.loads(val) if val else [])
                    else:
                        setattr(job, attr, val if val else [])

            # Detect sponsorship status
            desc_text = job.description_full or job.description_snippet
            job.sponsorship_status = detect_sponsorship_status(desc_text)

            # Detect experience level
            job.experience_level = detect_experience_level(job.title)

            # Apply lifecycle-aware visibility gates
            is_visible, filter_reason = _apply_visibility_gates(
                {**row, "match_score": job.match_score,
                 "sponsorship_status": job.sponsorship_status,
                 "experience_level": job.experience_level},
                search, cfg.score_threshold,
                require_sponsorship=require_sponsorship,
                allowed_experience_levels=allowed_experience_levels,
            )
            job.is_visible = is_visible
            job.filter_reason = filter_reason

            # Update DB
            db.update_job_scoring(
                job_id=row["id"],
                match_score=job.match_score,
                recommendation=job.recommendation,
                strong_matches=job.strong_matches,
                missing_keywords=job.missing_keywords,
                red_flags=job.red_flags,
                experience_alignment=job.experience_alignment,
                is_visible=job.is_visible,
                filter_reason=job.filter_reason,
                resume_improvement_prompt=job.resume_improvement_prompt,
                sponsorship_status=job.sponsorship_status,
                experience_level=job.experience_level,
            )

            if job.is_visible:
                visible_count += 1
            else:
                hidden_count += 1

        logger.info(f"Recompute done: {visible_count} visible, {hidden_count} hidden")
        return {"total": len(all_rows), "visible": visible_count, "hidden": hidden_count}

    finally:
        db.close()


def _apply_visibility_gates(
    row: dict, search: SearchConfig, score_threshold: int,
    require_sponsorship: bool = True,
    allowed_experience_levels: list[str] | None = None,
) -> tuple[bool, str]:
    """Apply visibility gates to a single DB row. Returns (is_visible, filter_reason).

    Lifecycle rules:
      - Protected states (Applied/Interviewing/Offer): ALWAYS visible, skip gates.
      - Terminal states (Rejected/Withdrawn/Archived): ALWAYS hidden.
      - Discovered: normal gate processing.

    Gate order (for Discovered jobs):
      1. Experience level
      2. Location / Remote
      3. Sponsorship
      4. Score threshold
    """
    job_status = row.get("job_status", "Discovered")

    # Protected states are always visible
    if job_status in PROTECTED_STATES:
        return True, ""

    # Terminal states are always hidden
    if job_status in TERMINAL_STATES:
        return False, f"Status: {job_status}"

    # Normal gate processing for Discovered jobs
    title = row.get("title", "")
    location = row.get("location", "")
    match_score = row.get("match_score", 0)

    # Gate 1: Experience level
    level = row.get("experience_level") or detect_experience_level(title)
    if allowed_experience_levels is not None and level and level not in allowed_experience_levels:
        return False, f"Experience level: {level}"

    # Gate 2: Location
    if search.location or search.remote_only:
        loc = location.lower()
        if search.remote_only and search.location:
            if "remote" not in loc or search.location.lower() not in loc:
                return False, f"Not remote in {search.location} (loc: {location})"
        elif search.remote_only:
            if "remote" not in loc:
                return False, f"Not remote (loc: {location})"
        elif search.location:
            if search.location.lower() not in loc:
                return False, f"Location mismatch (want: {search.location}, got: {location})"

    # Gate 3: Sponsorship
    if require_sponsorship:
        spons = row.get("sponsorship_status", "unknown")
        if spons == "not_sponsored":
            return False, "No visa sponsorship provided"

    # Gate 4: Score threshold
    if match_score < score_threshold:
        return False, f"Score {match_score} < threshold {score_threshold}"

    return True, ""


def apply_filters_only(
    cfg: AppConfig,
    search_override: SearchConfig | None = None,
    require_sponsorship: bool = True,
    allowed_experience_levels: list[str] | None = None,
) -> dict:
    """Re-apply visibility filters on ALL existing jobs without re-scoring.

    This is fast — no API calls, no resume loading, no scoring.
    Only re-evaluates: experience gate, location, sponsorship, score threshold.
    Updates is_visible and filter_reason in the DB.

    Returns:
        dict with keys: total, visible, hidden.
    """
    search = search_override or cfg.search
    db = get_db(cfg)

    try:
        all_rows = db.get_all_jobs_raw()
        if not all_rows:
            return {"total": 0, "visible": 0, "hidden": 0}

        logger.info(f"Apply filters: processing {len(all_rows)} jobs")

        visible_count = 0
        hidden_count = 0

        for row in all_rows:
            # Re-detect sponsorship if not already stored
            spons = row.get("sponsorship_status", "unknown")
            if spons == "unknown" or not spons:
                desc = row.get("description_full") or row.get("description_snippet", "")
                spons = detect_sponsorship_status(desc)
                if spons != "unknown":
                    row["sponsorship_status"] = spons

            is_visible, filter_reason = _apply_visibility_gates(
                row, search, cfg.score_threshold,
                require_sponsorship=require_sponsorship,
                allowed_experience_levels=allowed_experience_levels,
            )

            # Update DB only if changed
            old_vis = row.get("is_visible", True)
            old_reason = row.get("filter_reason", "")
            if old_vis in (True, 1):
                old_vis = True
            else:
                old_vis = False

            if is_visible != old_vis or filter_reason != old_reason:
                db.update_job_visibility(row["id"], is_visible, filter_reason)

            if is_visible:
                visible_count += 1
            else:
                hidden_count += 1

        logger.info(f"Filters applied: {visible_count} visible, {hidden_count} hidden")
        return {"total": len(all_rows), "visible": visible_count, "hidden": hidden_count}

    finally:
        db.close()


def run_discovery(
    cfg: AppConfig,
    max_probes: int = 50,
    custom_companies: list[str] | None = None,
) -> dict:
    """Discover new ATS company slugs.

    Sources company names from:
      1. User-provided custom names (probed first)
      2. YC's Algolia API (automatic)

    Derives candidate slugs, probes Greenhouse/Lever/Ashby APIs,
    and saves valid slugs to the DB.

    Returns:
        dict with keys: company_names, custom_companies, greenhouse,
        lever, ashby, total_new.
    """
    db = get_db(cfg)
    try:
        # 1. Collect company names: custom first, then YC
        company_names: list[str] = []
        if custom_companies:
            company_names.extend(custom_companies)

        yc_names = fetch_yc_company_names(max_pages=3)
        logger.info(f"[Discovery] Fetched {len(yc_names)} company names from YC")

        # Deduplicate (case-insensitive) while preserving order
        seen = {n.lower() for n in company_names}
        for name in yc_names:
            if name.lower() not in seen:
                seen.add(name.lower())
                company_names.append(name)

        logger.info(
            f"[Discovery] {len(company_names)} total company names "
            f"({len(custom_companies) if custom_companies else 0} custom, "
            f"{len(yc_names)} YC)"
        )

        # 2. Load known slugs (config + DB)
        known_slugs: set[str] = set()
        for slugs in cfg.companies.values():
            if isinstance(slugs, list):
                known_slugs.update(slugs)
        known_slugs |= db.get_known_slugs()
        logger.info(f"[Discovery] {len(known_slugs)} known slugs (config + DB)")

        # 3. Discover new slugs
        discovered = discover_new_slugs(
            company_names, known_slugs, max_probes=max_probes,
        )

        # 4. Save to DB
        saved = db.save_discovered_slugs(discovered)

        result = {
            "company_names": len(company_names),
            "custom_companies": len(custom_companies) if custom_companies else 0,
            "greenhouse": len(discovered["greenhouse"]),
            "lever": len(discovered["lever"]),
            "ashby": len(discovered["ashby"]),
            "total_new": saved,
        }
        logger.info(
            f"[Discovery] Complete: {saved} new slugs saved "
            f"(GH: {result['greenhouse']}, Lever: {result['lever']}, "
            f"Ashby: {result['ashby']})"
        )
        return result

    finally:
        db.close()
