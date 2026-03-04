"""Fetch jobs from WeWorkRemotely public RSS feeds."""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from src.fetchers.base import BaseFetcher
from src.models import Job

logger = logging.getLogger(__name__)

# Public RSS feeds covering programming, devops, and data roles
RSS_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
]


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss_date(date_str: str) -> datetime | None:
    """Parse RSS pubDate format: 'Mon, 01 Jan 2024 12:00:00 +0000'."""
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


class WeWorkRemotelyFetcher(BaseFetcher):
    """Fetch dev/data jobs from WeWorkRemotely RSS feeds."""

    source_name = "WeWorkRemotely"

    def fetch(self, _slug: str = "") -> list[Job]:
        all_jobs: list[Job] = []
        for feed_url in RSS_FEEDS:
            try:
                jobs = self._fetch_feed(feed_url)
                all_jobs.extend(jobs)
            except Exception as e:
                logger.warning(f"[WeWorkRemotely] Feed failed {feed_url}: {e}")
        logger.info(f"[WeWorkRemotely] Fetched {len(all_jobs)} jobs from {len(RSS_FEEDS)} feeds")
        return all_jobs

    def _fetch_feed(self, feed_url: str) -> list[Job]:
        resp = requests.get(
            feed_url,
            headers={"User-Agent": "job-tracker/1.0"},
            timeout=15,
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return []

        jobs: list[Job] = []
        for item in channel.findall("item"):
            raw_title = item.findtext("title", "")
            link = item.findtext("link", "")
            description = _strip_html(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "")

            # Title format is often "Company: Job Title"
            if ":" in raw_title:
                company, title = raw_title.split(":", 1)
                company = company.strip()
                title = title.strip()
            else:
                company = ""
                title = raw_title.strip()

            if not title or not link:
                continue

            date_posted = _parse_rss_date(pub_date) if pub_date else None
            snippet = description[:500]

            jobs.append(
                Job(
                    source=self.source_name,
                    company=company,
                    title=title,
                    location="Remote",
                    url=link,
                    employment_type="Remote",
                    date_posted=date_posted,
                    description_snippet=snippet,
                    description_full=description,
                )
            )

        return jobs

    def fetch_many(self, slugs: list[str]) -> list[Job]:
        """Portal fetcher — ignores slugs, fetches everything once."""
        try:
            return self.fetch()
        except Exception as e:
            logger.warning(f"[{self.source_name}] failed – {e}")
            return []
