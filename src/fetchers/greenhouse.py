"""Fetch jobs from Greenhouse public boards API."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import requests

from src.fetchers.base import BaseFetcher
from src.models import Job

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class GreenhouseFetcher(BaseFetcher):
    source_name = "Greenhouse"

    def fetch(self, company_slug: str) -> list[Job]:
        url = BASE_URL.format(slug=company_slug)
        resp = requests.get(url, params={"content": "true"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        jobs: list[Job] = []
        for item in data.get("jobs", []):
            location_name = ""
            if item.get("location"):
                location_name = item["location"].get("name", "")

            content = item.get("content", "")
            full_text = _strip_html(content)
            snippet = full_text[:500] if full_text else ""

            updated_at = item.get("updated_at")
            date_posted = None
            if updated_at:
                try:
                    date_posted = datetime.fromisoformat(
                        updated_at.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except (ValueError, TypeError):
                    pass

            departments = item.get("departments", [])
            dept = departments[0]["name"] if departments else ""

            abs_url = item.get("absolute_url", "")

            jobs.append(
                Job(
                    source=self.source_name,
                    company=company_slug,
                    title=item.get("title", ""),
                    location=location_name,
                    url=abs_url,
                    department=dept,
                    date_posted=date_posted,
                    description_snippet=snippet,
                    description_full=full_text,
                )
            )

        return jobs
