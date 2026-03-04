"""Data models for job postings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# ── Job lifecycle states ─────────────────────────────────
# Discovered → Applied → Interviewing → Offer
#                                     → Rejected
#                        → Withdrawn
# Discovered (stale 30d) → Archived
JOB_LIFECYCLE_STATES = [
    "Discovered",
    "Applied",
    "Interviewing",
    "Offer",
    "Rejected",
    "Withdrawn",
    "Archived",
]

# Jobs in these states are ALWAYS visible — skip pipeline visibility gates
PROTECTED_STATES = {"Applied", "Interviewing", "Offer"}

# Jobs in these states are ALWAYS hidden in default view
TERMINAL_STATES = {"Rejected", "Withdrawn", "Archived"}


@dataclass
class Job:
    """Normalized job posting."""
    source: str                          # Greenhouse / Lever / Ashby
    company: str
    title: str
    location: str
    url: str
    department: str = ""
    employment_type: str = ""
    date_posted: datetime | None = None
    date_found: datetime = field(default_factory=datetime.utcnow)
    description_snippet: str = ""
    description_full: str = ""           # full JD text for scoring

    # Scoring results (populated after scoring)
    match_score: int = 0
    recommendation: str = ""             # Apply Immediately / Apply With Tweaks / Low Match – Skip
    strong_matches: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    experience_alignment: str = ""       # e.g. "Required: 4 yrs | Yours: 0-3 yrs"
    resume_improvement_prompt: str = ""  # AI-ready text for resume tailoring

    # Tracking (legacy field kept for backward compat)
    status: str = "Not Applied"
    notes: str = ""
    roles_matched: list[str] = field(default_factory=list)  # which searched roles matched

    # Lifecycle
    job_status: str = "Discovered"       # one of JOB_LIFECYCLE_STATES
    applied_at: datetime | None = None   # set on first transition to "Applied"
    updated_at: datetime | None = None   # set on every status change

    # Sponsorship
    sponsorship_status: str = "unknown"  # "sponsored" / "not_sponsored" / "unknown"

    # Experience level (detected from title)
    experience_level: str = ""           # "Intern" / "Entry Level" / "Mid Level" / "Senior" / "Staff+"

    # Visibility (experience gate / recompute)
    is_visible: bool = True
    filter_reason: str = ""

    def canonical_key(self) -> str:
        """Key used for deduplication."""
        return f"{self.company.lower().strip()}|{self.title.lower().strip()}|{self.location.lower().strip()}"
