"""Fetch jobs from Wellfound (AngelList Talent).

Wellfound does not provide a public API. This fetcher attempts to use
their public job listing endpoint. If it fails, it returns an empty
list gracefully — no aggressive scraping.
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

# Wellfound GraphQL endpoint for public job listings
GRAPHQL_URL = "https://wellfound.com/graphql"

# Query for software/data/ML roles
GRAPHQL_QUERY = """
query JobListings($page: Int!) {
  jobListings(
    page: $page
    perPage: 50
    roleTypes: ["Software Engineer", "Data Engineer", "Machine Learning"]
  ) {
    startupJobs {
      title
      slug
      remoteOk
      primaryRoleTitle
      locationNames
      compensation
      startup {
        name
        companyUrl
      }
      description
      postedAt
    }
  }
}
"""


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class WellfoundFetcher(BaseFetcher):
    """Fetch startup jobs from Wellfound (best-effort, no scraping)."""

    source_name = "Wellfound"

    def fetch(self, _slug: str = "") -> list[Job]:
        try:
            return self._fetch_graphql()
        except Exception as e:
            logger.warning(
                f"[Wellfound] GraphQL fetch failed (expected — no public API): {e}"
            )
            return []

    def _fetch_graphql(self) -> list[Job]:
        """Attempt to use the public GraphQL endpoint."""
        resp = requests.post(
            GRAPHQL_URL,
            json={"query": GRAPHQL_QUERY, "variables": {"page": 1}},
            headers={
                "User-Agent": "job-tracker/1.0",
                "Content-Type": "application/json",
            },
            timeout=15,
        )

        if resp.status_code != 200:
            logger.info(f"[Wellfound] Status {resp.status_code} — skipping")
            return []

        data = resp.json()
        listings = (
            data.get("data", {})
            .get("jobListings", {})
            .get("startupJobs", [])
        )

        if not listings:
            logger.info("[Wellfound] No listings returned")
            return []

        jobs: list[Job] = []
        for item in listings:
            title = item.get("title", "")
            startup = item.get("startup", {})
            company = startup.get("name", "")
            if not title or not company:
                continue

            description = _strip_html(item.get("description", ""))
            snippet = description[:500]

            slug = item.get("slug", "")
            url = f"https://wellfound.com/jobs/{slug}" if slug else ""

            locations = item.get("locationNames", [])
            location = ", ".join(locations) if locations else ""
            if item.get("remoteOk"):
                location = f"Remote, {location}" if location else "Remote"

            date_posted = None
            posted_at = item.get("postedAt")
            if posted_at:
                try:
                    date_posted = datetime.fromisoformat(
                        posted_at.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except (ValueError, TypeError):
                    pass

            jobs.append(
                Job(
                    source=self.source_name,
                    company=company,
                    title=title,
                    location=location,
                    url=url,
                    department=item.get("primaryRoleTitle", ""),
                    date_posted=date_posted,
                    description_snippet=snippet,
                    description_full=description,
                )
            )

        logger.info(f"[Wellfound] Fetched {len(jobs)} jobs")
        return jobs

    def fetch_many(self, slugs: list[str]) -> list[Job]:
        """Portal fetcher — ignores slugs, fetches everything once."""
        try:
            return self.fetch()
        except Exception as e:
            logger.warning(f"[{self.source_name}] failed – {e}")
            return []
