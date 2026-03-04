"""Fetch jobs from Ashby public job board API."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import requests

from src.fetchers.base import BaseFetcher
from src.models import Job

logger = logging.getLogger(__name__)

# Ashby's public posting API endpoint
BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class AshbyFetcher(BaseFetcher):
    """Fetch from Ashby public posting API.

    Ashby provides a public job board API at:
      GET https://api.ashbyhq.com/posting-api/job-board/{slug}
    which returns JSON with a 'jobs' array.

    If the endpoint changes or is unavailable for a company, this fetcher
    logs a warning and returns an empty list without breaking the pipeline.
    """

    source_name = "Ashby"

    def fetch(self, company_slug: str) -> list[Job]:
        url = BOARD_URL.format(slug=company_slug)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"[Ashby] Could not fetch {company_slug}: {e}")
            return []

        data = resp.json()
        raw_jobs = data.get("jobs", [])
        if not isinstance(raw_jobs, list):
            return []

        jobs: list[Job] = []
        for item in raw_jobs:
            title = item.get("title", "")
            location = item.get("location", "") or ""
            department = item.get("departmentName", "") or item.get("department", "") or ""
            employment_type = item.get("employmentType", "") or ""

            # Description
            desc_html = item.get("descriptionHtml", "") or item.get("description", "")
            full_text = _strip_html(desc_html)
            snippet = full_text[:500]

            # URL
            posting_url = item.get("jobUrl", "") or item.get("applyUrl", "") or ""

            # Date
            published = item.get("publishedDate") or item.get("publishedAt") or item.get("createdAt")
            date_posted = None
            if published:
                try:
                    if isinstance(published, (int, float)):
                        date_posted = datetime.utcfromtimestamp(published / 1000)
                    else:
                        date_posted = datetime.fromisoformat(
                            str(published).replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                except (ValueError, TypeError, OSError):
                    pass

            jobs.append(
                Job(
                    source=self.source_name,
                    company=company_slug,
                    title=title,
                    location=location,
                    url=posting_url,
                    department=department,
                    employment_type=employment_type,
                    date_posted=date_posted,
                    description_snippet=snippet,
                    description_full=full_text,
                )
            )

        return jobs
