"""Deduplication logic for job postings."""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

from src.models import Job

logger = logging.getLogger(__name__)


def _canonicalize_url(url: str) -> str:
    """Normalize a URL by removing query params and fragments."""
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def deduplicate(jobs: list[Job], existing_keys: set[str]) -> list[Job]:
    """Remove duplicates from a batch of jobs.

    Deduplication is done by:
      1. Canonical URL match
      2. (company + title + location) key match
    Both are checked against existing DB keys and within the current batch.

    When a duplicate is found within the batch, its roles_matched are merged
    into the first occurrence so no matched role is lost.
    """
    seen_keys: set[str] = set(existing_keys)
    seen_urls: set[str] = set()
    key_to_job: dict[str, Job] = {}
    unique: list[Job] = []

    for job in jobs:
        canon_url = _canonicalize_url(job.url)
        key = job.canonical_key()

        if canon_url in seen_urls or key in seen_keys:
            # Merge roles into the existing job if it's in this batch
            if key in key_to_job:
                existing = key_to_job[key]
                existing.roles_matched = sorted(
                    set(existing.roles_matched) | set(job.roles_matched)
                )
            continue

        seen_urls.add(canon_url)
        seen_keys.add(key)
        key_to_job[key] = job
        unique.append(job)

    removed = len(jobs) - len(unique)
    if removed:
        logger.info(f"Dedup: removed {removed} duplicates, kept {len(unique)}")
    return unique
