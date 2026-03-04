"""Resume-driven relevance scoring.

Replaces strict title matching with a soft relevance score:
  Relevance = keyword_overlap * 0.6 + title_similarity * 0.2 + role_boost * 0.2

Jobs with relevance >= 40 are kept (instead of requiring exact title match).
"""

from __future__ import annotations

import logging
import re

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# ── Role title expansions ────────────────────────────────────
# Maps each high-level role to related job titles for role boost scoring.

TITLE_EXPANSIONS: dict[str, list[str]] = {
    "software engineer": [
        "software engineer", "software developer", "backend engineer",
        "frontend engineer", "fullstack engineer", "full stack engineer",
        "full-stack engineer", "platform engineer", "application engineer",
        "web developer", "api engineer", "systems engineer",
        "infrastructure engineer", "site reliability engineer",
        "devops engineer", "cloud engineer",
    ],
    "data engineer": [
        "data engineer", "analytics engineer", "data platform engineer",
        "etl engineer", "big data engineer", "data infrastructure engineer",
        "data developer", "data pipeline engineer",
    ],
    "ai engineer": [
        "ai engineer", "ml engineer", "machine learning engineer",
        "applied ml", "applied ai", "ai/ml engineer",
        "nlp engineer", "deep learning engineer", "computer vision engineer",
        "research engineer",
    ],
}

# ── Stop words (excluded from keyword extraction) ────────────
_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "was", "one", "our", "out", "has", "have", "with", "this", "that",
    "from", "they", "been", "said", "each", "which", "their", "will", "other",
    "about", "many", "then", "them", "these", "some", "would", "make", "like",
    "into", "over", "such", "your", "than", "its", "what", "very", "when",
    "come", "could", "now", "time", "may", "also", "more", "work", "year",
    "years", "experience", "team", "role", "company", "job", "position",
    "responsibilities", "requirements", "qualifications", "ability", "strong",
    "excellent", "preferred", "required", "including", "working", "knowledge",
    "understanding", "using", "building", "developing", "etc", "must", "should",
    "able", "well", "new", "use", "used", "help", "part", "join",
    "looking", "seeking", "ideal", "candidate", "opportunity", "passionate",
    "love", "great", "world", "best", "every", "just", "being", "most",
    "only", "still", "both", "between", "own", "same", "while", "where",
    "does", "did", "get", "got", "how", "much", "need", "first", "last",
    "long", "way", "day", "too", "any", "who", "why", "let", "see", "take",
    "across", "around", "within", "through", "along", "want", "open",
})

# Multi-word tech terms to extract as single units
_MULTI_WORD_TERMS = [
    "machine learning", "deep learning", "natural language processing",
    "computer vision", "data engineering", "data science", "data pipeline",
    "data pipelines", "distributed systems", "version control",
    "rest api", "restful api", "graphql api",
    "data warehouse", "data lake", "data modeling", "data mesh",
    "real time", "stream processing", "batch processing",
    "unit testing", "integration testing",
    "feature engineering", "feature store",
    "neural network", "large language model", "generative ai",
    "cloud computing", "big data", "data quality",
    "event driven", "microservices",
    "infrastructure as code", "software development",
    "full stack", "front end", "back end",
    "site reliability", "platform engineering",
    "apache spark", "apache kafka", "apache airflow", "apache flink",
    "amazon web services", "google cloud", "microsoft azure",
]


def extract_resume_keywords(resume_text: str) -> set[str]:
    """Extract tech skills, frameworks, and tools from resume text.

    Returns a set of lowercase keywords (both single and multi-word).
    """
    text_lower = resume_text.lower()
    keywords: set[str] = set()

    # Multi-word terms
    for term in _MULTI_WORD_TERMS:
        if term in text_lower:
            keywords.add(term)

    # Single-word tokens (alphanumeric + tech chars like +, #, ., -)
    tokens = set(re.findall(r"[a-z][a-z0-9+#.\-]{1,30}", text_lower))
    keywords |= {t for t in tokens if t not in _STOP_WORDS and len(t) >= 2}

    return keywords


def compute_relevance_score(
    job_title: str,
    job_description: str,
    resume_keywords: set[str],
    roles: list[str],
    expanded_role_titles: dict[str, list[str]] | None = None,
) -> tuple[float, list[str]]:
    """Compute a relevance score (0-100) for a job.

    Components:
      keyword_overlap (0.6) — resume tech keywords found in the job text
      title_similarity (0.2) — best fuzzy match between title and searched roles
      role_boost (0.2)       — bonus if title matches an expanded role variant

    Returns:
        (score, list of matched role names)
    """
    job_text = (job_title + " " + job_description).lower()
    title_lower = job_title.lower()

    # ── 1. Keyword overlap (0-100) ─────────────────────────
    if resume_keywords:
        job_tokens = set(re.findall(r"[a-z][a-z0-9+#.\-]{1,30}", job_text))
        job_tokens = {t for t in job_tokens if t not in _STOP_WORDS and len(t) >= 2}

        # Single-token matches
        token_overlap = job_tokens & resume_keywords
        # Multi-word matches
        multi_overlap = {
            term for term in resume_keywords
            if " " in term and term in job_text
        }
        overlap_count = len(token_overlap) + len(multi_overlap)
        # Scale: ~25 matches → 100 (4 points per match)
        keyword_score = min(100.0, overlap_count * 4.0)
    else:
        keyword_score = 50.0  # No resume available — neutral score

    # ── 2. Title similarity (0-100) ────────────────────────
    best_title_sim = 0.0
    for role in roles:
        sim = fuzz.token_sort_ratio(title_lower, role.lower())
        best_title_sim = max(best_title_sim, sim)
    title_score = best_title_sim

    # ── 3. Role boost (0-100) ──────────────────────────────
    #    Strict: only award if title contains an expanded variant.
    #    Fuzzy title similarity is already captured in component 2.
    matched_roles: list[str] = []
    role_score = 0.0
    expanded = expanded_role_titles or {}

    for role in roles:
        role_key = role.lower().strip()
        variants = expanded.get(role_key, [role_key])
        for variant in variants:
            if variant.lower() in title_lower:
                if role not in matched_roles:
                    matched_roles.append(role)
                role_score = 100.0
                break

    # ── Weighted final ─────────────────────────────────────
    final = keyword_score * 0.6 + title_score * 0.2 + role_score * 0.2
    return max(0.0, min(100.0, final)), matched_roles
