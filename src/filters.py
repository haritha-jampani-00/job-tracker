"""Filtering logic to reduce noise and overload."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from src.config import SearchConfig
from src.models import Job
from src.relevance import TITLE_EXPANSIONS, compute_relevance_score

logger = logging.getLogger(__name__)

# Relevance score threshold — jobs below this are dropped pre-scoring.
RELEVANCE_THRESHOLD = 35

# ── Experience level detection ────────────────────────────

EXPERIENCE_LEVELS = ["Intern", "Entry Level", "Mid Level", "Senior", "Staff+"]

# Ordered most-senior first so the first match wins (e.g. "Staff" before "Senior").
_LEVEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:principal|distinguished|fellow)\b", re.I), "Staff+"),
    (re.compile(r"\b(?:director|vp|vice\s+president)\b", re.I), "Staff+"),
    (re.compile(r"\bstaff\b", re.I), "Staff+"),
    (re.compile(r"\blead\b", re.I), "Staff+"),
    (re.compile(r"\b(?:senior|sr\.?)\b", re.I), "Senior"),
    (re.compile(r"\bmid[- ]?level\b", re.I), "Mid Level"),
    (re.compile(r"\b(?:junior|jr\.?)\b", re.I), "Entry Level"),
    (re.compile(r"\b(?:entry[- ]?level|new\s+grad|associate)\b", re.I), "Entry Level"),
    (re.compile(r"\bintern(?:ship)?\b", re.I), "Intern"),
]


def detect_experience_level(text: str) -> str:
    """Detect experience level from job title text.

    Scans for seniority keywords (most-senior first).
    Returns one of EXPERIENCE_LEVELS, or "" if no keyword detected.
    """
    if not text:
        return ""
    for pat, level in _LEVEL_PATTERNS:
        if pat.search(text):
            return level
    return ""


# ── Experience year extraction ────────────────────────────

# Patterns to extract minimum years required from job descriptions.
# Ordered most-specific first so the correct number is captured.
_EXPERIENCE_PATTERNS = [
    # "at least 4 years", "minimum 4 years", "a minimum of 4 years"
    re.compile(r"(?:at\s+least|minimum|a\s+minimum\s+of)\s+(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I),
    # "4+ years", "4 years"
    re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.I),
    # "3-5 years" / "3 to 5 years" → take the lower bound
    re.compile(r"(\d{1,2})\s*[-–to]+\s*\d{1,2}\s*\+?\s*(?:years?|yrs?)", re.I),
    # "4 years of experience", "4 years experience"
    re.compile(r"(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)", re.I),
]

# Seniority keywords in job titles imply minimum experience.
# Used as fallback when no explicit "X years" is found in the text.
_SENIORITY_MIN_YEARS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\bprincipal\b", re.I), 10),
    (re.compile(r"\bstaff\b", re.I), 8),
    (re.compile(r"\blead\b", re.I), 7),
    (re.compile(r"\bsenior\b", re.I), 5),
    (re.compile(r"\bmid[- ]?level\b", re.I), 3),
    (re.compile(r"\bsr\.?\b", re.I), 5),
]


def extract_min_years_required(text: str) -> int | None:
    """Extract the minimum years of experience required from job text.

    Two-tier extraction:
      1. Explicit patterns: "4+ years", "at least 4 years", "3-5 years", etc.
      2. Seniority fallback: "Senior" → 5yr, "Staff" → 8yr, etc.
         (only used when no explicit year pattern is found)

    Returns the highest match found, or None if nothing detected.
    """
    # Tier 1: explicit year patterns in full text
    found: list[int] = []
    for pat in _EXPERIENCE_PATTERNS:
        for m in pat.finditer(text):
            found.append(int(m.group(1)))
    if found:
        return max(found)

    # Tier 2: seniority keywords (check title — first ~100 chars)
    title_area = text[:100]
    for pat, min_yr in _SENIORITY_MIN_YEARS:
        if pat.search(title_area):
            return min_yr

    return None


def _title_matches_query(title: str, role_query: str) -> bool:
    """Check if the job title is relevant to the role query.

    Uses a two-tier match:
      1. If the full query phrase appears in the title → match (e.g., "Data Engineer")
      2. Otherwise, require at least half the query words to appear as whole words
    This avoids matching "Data Scientist" when searching "Data Engineer".
    """
    title_lower = title.lower()
    query_lower = role_query.lower()

    # Exact phrase match (best signal)
    if query_lower in title_lower:
        return True

    # Word-level matching: require majority of query words
    query_words = query_lower.split()
    matched = sum(1 for w in query_words if re.search(rf"\b{re.escape(w)}\b", title_lower))
    return matched >= max(len(query_words) // 2 + 1, 2) if len(query_words) >= 2 else matched >= 1


# ── Role title expansions ────────────────────────────────
# Maps each high-level role to related job titles that should also match.
# This lets "Software Engineer" catch "Backend Engineer", "Platform Engineer", etc.

_TITLE_EXPANSIONS: dict[str, list[str]] = {
    "software engineer": [
        "software engineer", "software developer", "backend engineer",
        "frontend engineer", "fullstack engineer", "full stack engineer",
        "full-stack engineer", "platform engineer", "application engineer",
        "web developer", "api engineer", "systems engineer",
        "infrastructure engineer", "site reliability engineer",
        "devops engineer", "cloud engineer",
    ],
    "data engineer": [
        "data engineer", "analytics engineer", "data platform engineer",
        "etl engineer", "big data engineer", "data infrastructure engineer",
        "data developer", "data pipeline engineer",
    ],
    "ai engineer": [
        "ai engineer", "ml engineer", "machine learning engineer",
        "applied ml", "applied ai", "ai/ml engineer",
        "nlp engineer", "deep learning engineer", "computer vision engineer",
        "research engineer",
    ],
}


def _get_expanded_queries(role: str) -> list[str]:
    """Get expanded title queries for a role.

    If the role has known expansions, returns all related titles.
    Otherwise returns just the original role.
    """
    key = role.lower().strip()
    if key in _TITLE_EXPANSIONS:
        return _TITLE_EXPANSIONS[key]
    return [role]


def _title_matches_any_role(title: str, roles: list[str]) -> list[str]:
    """Return list of roles that match this title (using expanded titles).

    Each role is expanded to include related job titles.
    If any expanded title matches, the original role is returned.
    """
    matched_roles: list[str] = []
    for role in roles:
        expanded = _get_expanded_queries(role)
        for query in expanded:
            if _title_matches_query(title, query):
                matched_roles.append(role)
                break
    return matched_roles


def filter_by_relevance(
    jobs: list[Job],
    search: SearchConfig,
    resume_keywords: set[str] | None = None,
) -> list[Job]:
    """Pre-scoring relevance filter using soft scoring.

    Computes a relevance score (0-100) for each job based on:
      - keyword overlap with resume (60%)
      - fuzzy title similarity to searched roles (20%)
      - expanded role match boost (20%)

    Keeps jobs with relevance >= RELEVANCE_THRESHOLD (40).
    Also tags each kept job with its matched roles.
    """
    keywords = resume_keywords or set()
    filtered: list[Job] = []

    for job in jobs:
        description = job.description_full or job.description_snippet
        score, matched = compute_relevance_score(
            job_title=job.title,
            job_description=description,
            resume_keywords=keywords,
            roles=search.roles,
            expanded_role_titles=TITLE_EXPANSIONS,
        )
        if score >= RELEVANCE_THRESHOLD:
            if matched:
                job.roles_matched = sorted(set(job.roles_matched) | set(matched))
            elif not job.roles_matched:
                # No direct role match but relevant by keywords — assign all roles
                job.roles_matched = list(search.roles)
            filtered.append(job)

    removed = len(jobs) - len(filtered)
    if removed:
        logger.info(f"Relevance filter: removed {removed} jobs below threshold {RELEVANCE_THRESHOLD}")
    return filtered


def filter_by_location(jobs: list[Job], search: SearchConfig) -> list[Job]:
    """Filter by location preference if set."""
    if not search.location and not search.remote_only:
        return jobs

    result: list[Job] = []
    for job in jobs:
        loc = job.location.lower()
        if search.remote_only and search.location:
            # Both set: must be remote AND match location
            if "remote" in loc and search.location.lower() in loc:
                result.append(job)
        elif search.remote_only:
            if "remote" in loc:
                result.append(job)
        elif search.location:
            if search.location.lower() in loc:
                result.append(job)
    return result


def filter_by_freshness(jobs: list[Job], freshness_days: int) -> list[Job]:
    """Keep jobs found within the freshness window.

    Uses date_found (when this app first saw the job) as the primary
    reference.  Some sources (Lever, Ashby) return the original posting
    date which can be months old — using date_posted would incorrectly
    discard them.  A job is kept if:
      1. date_found is within the window (always true for fresh fetches), OR
      2. date_posted is within the window (for sources with accurate dates), OR
      3. Neither date is available (benefit of the doubt).
    """
    cutoff = datetime.utcnow() - timedelta(days=freshness_days)
    result: list[Job] = []
    for job in jobs:
        if job.date_found and job.date_found >= cutoff:
            result.append(job)
        elif job.date_posted and job.date_posted >= cutoff:
            result.append(job)
        elif not job.date_found and not job.date_posted:
            result.append(job)
    return result


def filter_by_score(jobs: list[Job], threshold: int) -> list[Job]:
    """Keep only jobs above the score threshold."""
    return [j for j in jobs if j.match_score >= threshold]


def apply_experience_gate(
    jobs: list[Job],
    allowed_levels: list[str] | None = None,
) -> list[Job]:
    """Filter jobs by detected experience level.

    Detects the experience level from each job's title and sets
    job.experience_level. If allowed_levels is provided, hides jobs
    whose level is not in the allowed list.

    Jobs with unknown level ("") always pass through.
    All jobs are returned (none removed — visibility flag only).
    """
    allowed = set(allowed_levels) if allowed_levels is not None else None
    hidden = 0

    for job in jobs:
        level = detect_experience_level(job.title)
        job.experience_level = level

        if allowed is not None and level and level not in allowed:
            job.is_visible = False
            job.filter_reason = f"Experience level: {level}"
            hidden += 1
            logger.debug(
                f"Experience gate: hiding '{job.title}' at {job.company} "
                f"(level: {level})"
            )
        else:
            if not job.filter_reason:
                job.is_visible = True

    if hidden:
        logger.info(f"Experience gate: hiding {hidden} jobs by level")
    return jobs


def cap_results(jobs: list[Job], max_results: int) -> list[Job]:
    """Sort by score descending and cap at max_results."""
    jobs.sort(key=lambda j: j.match_score, reverse=True)
    return jobs[:max_results]


# ── Visa sponsorship detection ──────────────────────────

# Patterns indicating NO sponsorship (case-insensitive).
_NO_SPONSORSHIP_PATTERNS = [
    re.compile(r"no\s+(?:visa\s+)?sponsorship", re.I),
    re.compile(r"cannot\s+sponsor", re.I),
    re.compile(r"can\s?not\s+sponsor", re.I),
    re.compile(r"unable\s+to\s+sponsor", re.I),
    re.compile(r"not\s+(?:able|willing)\s+to\s+sponsor", re.I),
    re.compile(r"must\s+be\s+(?:legally\s+)?authorized\s+to\s+work", re.I),
    re.compile(r"no\s+visa\s+support", re.I),
    re.compile(r"no\s+h[- ]?1b", re.I),
    re.compile(r"us\s+work\s+authorization\s+required", re.I),
    re.compile(r"we\s+do\s+not\s+(?:provide|offer)\s+sponsorship", re.I),
    re.compile(r"(?:will|does)\s+not\s+(?:provide|offer)\s+(?:visa\s+)?sponsorship", re.I),
    re.compile(r"without\s+(?:requiring\s+)?(?:visa\s+)?sponsorship", re.I),
    re.compile(r"not\s+(?:eligible|available)\s+for\s+(?:visa\s+)?sponsorship", re.I),
]

# Patterns indicating YES sponsorship is available.
_YES_SPONSORSHIP_PATTERNS = [
    re.compile(r"visa\s+sponsorship\s+(?:is\s+)?available", re.I),
    re.compile(r"h[- ]?1b\s+(?:is\s+)?supported", re.I),
    re.compile(r"we\s+sponsor\s+visas?", re.I),
    re.compile(r"immigration\s+support\s+provided", re.I),
    re.compile(r"(?:will|can)\s+(?:provide|offer)\s+(?:visa\s+)?sponsorship", re.I),
    re.compile(r"sponsorship\s+(?:is\s+)?(?:available|offered|provided)", re.I),
]


def detect_sponsorship_status(text: str) -> str:
    """Detect visa sponsorship status from job description text.

    Returns:
        "not_sponsored" — explicit no-sponsorship language found
        "sponsored"     — explicit sponsorship-available language found
        "unknown"       — no clear signal either way
    """
    if not text:
        return "unknown"

    # Check negative patterns first (they tend to be more definitive)
    for pat in _NO_SPONSORSHIP_PATTERNS:
        if pat.search(text):
            return "not_sponsored"

    for pat in _YES_SPONSORSHIP_PATTERNS:
        if pat.search(text):
            return "sponsored"

    return "unknown"


def apply_sponsorship_filter(
    jobs: list[Job], require_sponsorship: bool,
) -> list[Job]:
    """Detect sponsorship status on each job and optionally hide non-sponsored ones.

    Always sets job.sponsorship_status. When require_sponsorship is True,
    jobs with status "not_sponsored" are marked invisible.

    Returns all jobs (none removed from list — visibility flag only).
    """
    hidden = 0
    for job in jobs:
        text = job.description_full or job.description_snippet
        job.sponsorship_status = detect_sponsorship_status(text)

        if require_sponsorship and job.sponsorship_status == "not_sponsored":
            job.is_visible = False
            job.filter_reason = "No visa sponsorship provided"
            hidden += 1

    if hidden:
        logger.info(f"Sponsorship filter: hiding {hidden} jobs (no sponsorship)")
    return jobs
