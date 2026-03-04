"""Fetch jobs from RemoteOK public API."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import requests

from src.fetchers.base import BaseFetcher
from src.models import Job

logger = logging.getLogger(__name__)

API_URL = "https://remoteok.com/api"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class RemoteOKFetcher(BaseFetcher):
    """Fetch dev/data/ML jobs from RemoteOK public JSON API."""

    source_name = "RemoteOK"

    def fetch(self, _slug: str = "") -> list[Job]:
        resp = requests.get(
            API_URL,
            headers={"User-Agent": "job-tracker/1.0"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        # First element is metadata/legal notice — skip it
        postings = data[1:] if len(data) > 1 else []

        jobs: list[Job] = []
        for item in postings:
            if not isinstance(item, dict):
                continue

            title = item.get("position", "")
            company = item.get("company", "")
            if not title or not company:
                continue

            description = _strip_html(item.get("description", ""))
            snippet = description[:500]

            date_posted = None
            epoch = item.get("epoch")
            if epoch:
                try:
                    date_posted = datetime.utcfromtimestamp(int(epoch))
                except (ValueError, TypeError, OSError):
                    pass

            location = item.get("location", "Remote") or "Remote"
            url = item.get("url", "")
            if url and not url.startswith("http"):
                url = f"https://remoteok.com{url}"

            tags = item.get("tags", [])
            employment_type = "Remote"

            jobs.append(
                Job(
                    source=self.source_name,
                    company=company,
                    title=title,
                    location=location,
                    url=url,
                    department=", ".join(tags[:3]) if tags else "",
                    employment_type=employment_type,
                    date_posted=date_posted,
                    description_snippet=snippet,
                    description_full=description,
                )
            )

        logger.info(f"[RemoteOK] Fetched {len(jobs)} jobs")
        return jobs

    def fetch_many(self, slugs: list[str]) -> list[Job]:
        """Portal fetcher — ignores slugs, fetches everything once."""
        try:
            return self.fetch()
        except Exception as e:
            logger.warning(f"[{self.source_name}] failed – {e}")
            return []
