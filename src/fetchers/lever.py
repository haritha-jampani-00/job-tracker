"""Fetch jobs from Lever public postings API."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import requests

from src.fetchers.base import BaseFetcher
from src.models import Job

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings/{slug}"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class LeverFetcher(BaseFetcher):
    source_name = "Lever"

    def fetch(self, company_slug: str) -> list[Job]:
        url = BASE_URL.format(slug=company_slug)
        resp = requests.get(url, params={"mode": "json"}, timeout=15)
        resp.raise_for_status()
        postings = resp.json()

        if not isinstance(postings, list):
            return []

        jobs: list[Job] = []
        for item in postings:
            # Build description from lists
            desc_parts: list[str] = []
            for lst in item.get("lists", []):
                desc_parts.append(lst.get("text", ""))
                desc_parts.append(
                    " ".join(_strip_html(li) for li in lst.get("content_list", []) if isinstance(li, str))
                )
            full_text = " ".join(desc_parts).strip()

            # Also include the opening description
            additional = item.get("descriptionPlain") or item.get("description", "")
            if additional:
                full_text = _strip_html(additional) + " " + full_text
            full_text = full_text.strip()
            snippet = full_text[:500]

            created_at = item.get("createdAt")
            date_posted = None
            if created_at:
                try:
                    date_posted = datetime.utcfromtimestamp(created_at / 1000)
                except (ValueError, TypeError, OSError):
                    pass

            categories = item.get("categories", {})
            location = categories.get("location", "") or ""
            department = categories.get("department", "") or ""
            commitment = categories.get("commitment", "") or ""

            jobs.append(
                Job(
                    source=self.source_name,
                    company=company_slug,
                    title=item.get("text", ""),
                    location=location,
                    url=item.get("hostedUrl", ""),
                    department=department,
                    employment_type=commitment,
                    date_posted=date_posted,
                    description_snippet=snippet,
                    description_full=full_text,
                )
            )

        return jobs
