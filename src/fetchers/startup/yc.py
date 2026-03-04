"""Fetch jobs from YC Work at a Startup.

Uses the public Algolia-backed search API that powers
workatastartup.com. If the endpoint changes, fails gracefully.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import requests

from src.fetchers.base import BaseFetcher
from src.models import Job

logger = logging.getLogger(__name__)

# Algolia search endpoint used by workatastartup.com
ALGOLIA_URL = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/WaaSJobs_production/query"
ALGOLIA_APP_ID = "45BWZJ1SGC"
ALGOLIA_API_KEY = "MjBjYjRiMzY0NzdhZWY0NjExY2NhZjYxMGIxYjc2MTAwNWFkNTkwNTc4NjgxYjJiMDRmMjQ4NTZhZTViMDlkZXRhZ0ZpbHRlcnM9"

# Simpler fallback: public JSON list
COMPANIES_URL = "https://www.workatastartup.com/companies.json"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class YCFetcher(BaseFetcher):
    """Fetch jobs from YC Work at a Startup (best-effort)."""

    source_name = "YC"

    def fetch(self, _slug: str = "") -> list[Job]:
        # Try Algolia search first, fall back to companies JSON
        jobs = self._fetch_algolia()
        if not jobs:
            jobs = self._fetch_companies_json()
        return jobs

    def _fetch_algolia(self) -> list[Job]:
        """Try the Algolia search API used by the site."""
        try:
            resp = requests.post(
                ALGOLIA_URL,
                json={
                    "params": "query=engineer&hitsPerPage=100",
                },
                headers={
                    "X-Algolia-Application-Id": ALGOLIA_APP_ID,
                    "X-Algolia-API-Key": ALGOLIA_API_KEY,
                    "User-Agent": "job-tracker/1.0",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )

            if resp.status_code != 200:
                logger.info(f"[YC] Algolia returned {resp.status_code}")
                return []

            data = resp.json()
            hits = data.get("hits", [])
            if not hits:
                return []

            jobs: list[Job] = []
            for item in hits:
                title = item.get("title", "")
                company = item.get("company_name", "")
                if not title or not company:
                    continue

                description = _strip_html(item.get("description", ""))
                snippet = description[:500]

                slug = item.get("slug", "")
                company_slug = item.get("company_slug", "")
                url = (
                    f"https://www.workatastartup.com/companies/{company_slug}"
                    if company_slug
                    else ""
                )

                location = item.get("pretty_location", "") or ""
                if item.get("remote"):
                    location = f"Remote, {location}" if location else "Remote"

                date_posted = None
                created_at = item.get("created_at")
                if created_at:
                    try:
                        date_posted = datetime.utcfromtimestamp(created_at)
                    except (ValueError, TypeError, OSError):
                        pass

                jobs.append(
                    Job(
                        source=self.source_name,
                        company=company,
                        title=title,
                        location=location,
                        url=url,
                        date_posted=date_posted,
                        description_snippet=snippet,
                        description_full=description,
                    )
                )

            logger.info(f"[YC] Algolia: fetched {len(jobs)} jobs")
            return jobs

        except Exception as e:
            logger.info(f"[YC] Algolia failed: {e}")
            return []

    def _fetch_companies_json(self) -> list[Job]:
        """Fallback: try the public companies JSON endpoint."""
        try:
            resp = requests.get(
                COMPANIES_URL,
                headers={"User-Agent": "job-tracker/1.0"},
                timeout=20,
            )

            if resp.status_code != 200:
                logger.info(f"[YC] companies.json returned {resp.status_code}")
                return []

            companies = resp.json()
            if not isinstance(companies, list):
                return []

            jobs: list[Job] = []
            for co in companies[:200]:  # Cap to avoid processing thousands
                company_name = co.get("name", "")
                company_slug = co.get("slug", "")
                for role in co.get("jobs", []):
                    title = role.get("title", "")
                    if not title or not company_name:
                        continue

                    description = _strip_html(role.get("description", ""))
                    url = role.get("url", "")
                    if not url and company_slug:
                        url = f"https://www.workatastartup.com/companies/{company_slug}"

                    location = role.get("pretty_location", "") or ""
                    if role.get("remote"):
                        location = f"Remote, {location}" if location else "Remote"

                    jobs.append(
                        Job(
                            source=self.source_name,
                            company=company_name,
                            title=title,
                            location=location,
                            url=url,
                            description_snippet=description[:500],
                            description_full=description,
                        )
                    )

            logger.info(f"[YC] companies.json: fetched {len(jobs)} jobs")
            return jobs

        except Exception as e:
            logger.warning(f"[YC] companies.json failed: {e}")
            return []

    def fetch_many(self, slugs: list[str]) -> list[Job]:
        """Portal fetcher — ignores slugs, fetches everything once."""
        try:
            return self.fetch()
        except Exception as e:
            logger.warning(f"[{self.source_name}] failed – {e}")
            return []
