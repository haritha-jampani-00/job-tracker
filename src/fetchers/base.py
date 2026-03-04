"""Base class for job fetchers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from src.models import Job

logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    """Abstract base for all job board fetchers."""

    source_name: str = "Unknown"

    @abstractmethod
    def fetch(self, company_slug: str) -> list[Job]:
        """Fetch jobs from a single company board. Returns normalized Job list."""
        ...

    def fetch_many(self, slugs: list[str]) -> list[Job]:
        """Fetch from multiple company slugs, handling errors gracefully."""
        all_jobs: list[Job] = []
        for slug in slugs:
            try:
                jobs = self.fetch(slug)
                logger.info(f"[{self.source_name}] {slug}: {len(jobs)} jobs")
                all_jobs.extend(jobs)
            except Exception as e:
                logger.warning(f"[{self.source_name}] {slug}: failed – {e}")
        return all_jobs
