"""Auto-discover Greenhouse/Lever/Ashby company slugs.

Takes company names (from YC directory, etc.), derives candidate slugs,
and probes ATS public APIs to verify which boards exist. Results are
cached in the DB to avoid re-probing on every run.
"""

from __future__ import annotations

import logging
import re
import time

import requests

logger = logging.getLogger(__name__)

# ── Slug derivation ─────────────────────────────────────

_STRIP_SUFFIXES = re.compile(
    r"\s*\b(inc\.?|ltd\.?|co\.?|corp\.?|llc|labs?|hq|ai|technologies|"
    r"technology|software|systems|group|holdings|solutions|io)\s*$",
    re.I,
)


def derive_slugs(company_name: str) -> list[str]:
    """Generate candidate ATS slugs from a company name.

    Strategies:
      1. Lowercase, strip corporate suffixes, replace spaces with hyphens
      2. No-separator variant (spaces removed)
      3. First-word-only variant (for multi-word names)

    Returns deduplicated list ordered by most likely first.
    """
    name = company_name.strip()
    if not name:
        return []

    # Strip suffixes
    cleaned = _STRIP_SUFFIXES.sub("", name).strip()
    if not cleaned:
        cleaned = name

    # Normalize: lowercase, keep only alphanumeric + spaces
    normalized = re.sub(r"[^a-z0-9 ]", "", cleaned.lower()).strip()
    if not normalized:
        return []

    # Variant 1: hyphenated (most common slug format)
    hyphenated = re.sub(r"\s+", "-", normalized)

    # Variant 2: no separator
    no_sep = re.sub(r"\s+", "", normalized)

    # Variant 3: first word only (for "Palo Alto Networks" → "paloalto" won't help,
    # but for "Notion Labs" → "notion" it does)
    first_word = normalized.split()[0] if " " in normalized else None

    # Deduplicate while preserving order
    seen: set[str] = set()
    slugs: list[str] = []
    for s in [hyphenated, no_sep, first_word]:
        if s and s not in seen and len(s) >= 2:
            seen.add(s)
            slugs.append(s)

    return slugs


# ── ATS probing ─────────────────────────────────────────

_TIMEOUT = 5  # seconds per probe request


def probe_greenhouse(slug: str) -> bool:
    """Check if a Greenhouse job board exists for this slug."""
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=_TIMEOUT,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def probe_lever(slug: str) -> bool:
    """Check if a Lever job board exists for this slug."""
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{slug}",
            params={"mode": "json"},
            timeout=_TIMEOUT,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def probe_ashby(slug: str) -> bool:
    """Check if an Ashby job board exists for this slug."""
    try:
        resp = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            timeout=_TIMEOUT,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


# Probe functions ordered by market share (Greenhouse is most common)
_PROBERS = [
    ("greenhouse", probe_greenhouse),
    ("lever", probe_lever),
    ("ashby", probe_ashby),
]


def probe_company(slug: str) -> str | None:
    """Probe all ATS APIs for a slug. Returns ATS type or None.

    Stops on first hit — companies typically use only one ATS.
    """
    for ats_type, probe_fn in _PROBERS:
        if probe_fn(slug):
            return ats_type
    return None


# ── YC company names ────────────────────────────────────

_ALGOLIA_URL = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/WaaSJobs_production/query"
_ALGOLIA_APP_ID = "45BWZJ1SGC"
_ALGOLIA_API_KEY = (
    "MjBjYjRiMzY0NzdhZWY0NjExY2NhZjYxMGIxYjc2MTAwNWFkNTkwNTc4NjgxYjJi"
    "MDRmMjQ4NTZhZTViMDlkZXRhZ0ZpbHRlcnM9"
)


def fetch_yc_company_names(max_pages: int = 3) -> list[str]:
    """Fetch unique company names from YC's Algolia index.

    Paginates through results to get broader coverage.
    Returns deduplicated list of company names.
    """
    seen: set[str] = set()
    names: list[str] = []

    for page in range(max_pages):
        try:
            resp = requests.post(
                _ALGOLIA_URL,
                json={"params": f"query=engineer&hitsPerPage=100&page={page}"},
                headers={
                    "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
                    "X-Algolia-API-Key": _ALGOLIA_API_KEY,
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                break

            hits = resp.json().get("hits", [])
            if not hits:
                break

            for hit in hits:
                name = hit.get("company_name", "").strip()
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)

        except Exception as e:
            logger.warning(f"[Discovery] YC Algolia page {page} failed: {e}")
            break

    logger.info(f"[Discovery] YC: {len(names)} unique company names")
    return names


# ── Discovery orchestrator ──────────────────────────────

def discover_new_slugs(
    company_names: list[str],
    known_slugs: set[str],
    max_probes: int = 50,
    probe_delay: float = 0.2,
) -> dict[str, list[tuple[str, str]]]:
    """Discover ATS slugs for unknown companies.

    Args:
        company_names: List of company names to check.
        known_slugs: Set of slugs already in config or DB (skip these).
        max_probes: Max number of API probes per run.
        probe_delay: Seconds to wait between probes (rate limiting).

    Returns:
        {"greenhouse": [(slug, company_name), ...],
         "lever": [...], "ashby": [...]}
    """
    results: dict[str, list[tuple[str, str]]] = {
        "greenhouse": [],
        "lever": [],
        "ashby": [],
    }
    probes_done = 0

    for company_name in company_names:
        if probes_done >= max_probes:
            logger.info(f"[Discovery] Reached probe limit ({max_probes}), stopping")
            break

        candidates = derive_slugs(company_name)
        # Skip if all candidates are already known
        if all(c in known_slugs for c in candidates):
            continue

        found = False
        for slug in candidates:
            if slug in known_slugs:
                continue

            if probes_done >= max_probes:
                break

            time.sleep(probe_delay)
            ats_type = probe_company(slug)
            probes_done += 1

            if ats_type:
                results[ats_type].append((slug, company_name))
                known_slugs.add(slug)
                logger.info(f"[Discovery] {company_name} → {slug} ({ats_type})")
                found = True
                break

        if not found and candidates:
            # All candidates probed, none matched — add to known to skip next time
            for c in candidates:
                known_slugs.add(c)

    total = sum(len(v) for v in results.values())
    logger.info(
        f"[Discovery] Done: {total} new boards found "
        f"(GH: {len(results['greenhouse'])}, "
        f"Lever: {len(results['lever'])}, "
        f"Ashby: {len(results['ashby'])}) "
        f"in {probes_done} probes"
    )
    return results
