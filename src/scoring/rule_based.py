"""Local, deterministic resume-aware scoring engine.

Scoring uses four weighted components:
  1. Title match      (20%) — do the searched roles appear in the job title?
  2. Keyword overlap   (40%) — must-have, nice-to-have, and avoid keywords
  3. Experience align  (20%) — does the JD's seniority match min/max years?
  4. Tech stack overlap (20%) — resume tokens vs. JD tokens
"""

from __future__ import annotations

import logging
import re

from src.config import SearchConfig
from src.models import Job

logger = logging.getLogger(__name__)

# ── Experience-level patterns ───────────────────────────────
YEARS_PATTERN = re.compile(r"(\d{1,2})\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)

SENIORITY_SIGNALS: dict[str, int] = {
    r"\bintern\b": 0,
    r"\bjunior\b": 0,
    r"\bentry[- ]?level\b": 0,
    r"\bmid[- ]?level\b": 3,
    r"\bsenior\b": 5,
    r"\blead\b": 7,
    r"\bstaff\b": 8,
    r"\bprincipal\b": 10,
    r"\bdirector\b": 12,
    r"\bvp\b": 15,
    r"\bvice\s+president\b": 15,
    r"\bfellow\b": 15,
    r"\bdistinguished\b": 15,
}

# Common English words to skip when comparing resume vs JD tokens.
# We keep tech terms (python, sql, aws, etc.) and filter out generic words.
_STOP_WORDS = frozenset({
    # Articles, pronouns, prepositions, conjunctions
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
    "her", "his", "him", "was", "one", "our", "out", "has", "have", "been",
    "will", "with", "this", "that", "from", "they", "were", "which", "their",
    "what", "about", "would", "make", "like", "just", "over", "such", "an",
    "take", "than", "them", "very", "some", "could", "into", "other", "as",
    "then", "these", "two", "more", "also", "its", "may", "how", "who", "at",
    "each", "she", "do", "when", "where", "why", "does", "did", "any", "be",
    "or", "if", "so", "no", "up", "on", "by", "to", "in", "it", "of", "we",
    "my", "me", "is", "am",
    # Common verbs
    "new", "work", "join", "help", "well", "go", "going", "went", "come",
    "made", "give", "find", "keep", "let", "put", "say", "said", "tell",
    "think", "know", "see", "get", "set", "run", "try", "ask", "show",
    "move", "live", "believe", "bring", "happen", "provide", "become",
    "leave", "feel", "seem", "allow", "lead", "begin", "grow", "open",
    "walk", "win", "offer", "remember", "consider", "appear", "buy", "wait",
    "serve", "send", "expect", "stay", "fall", "call", "add",
    # Time words
    "time", "first", "last", "long", "only", "after", "before", "day",
    "every", "during", "early", "today", "current", "currently",
    # Adjectives / adverbs
    "back", "little", "most", "much", "off", "own", "right", "still",
    "too", "should", "here", "even", "while", "being", "same", "under",
    "way", "both", "used", "need", "needs", "want", "able", "across",
    "along", "already", "among", "another", "around", "away", "became",
    "because", "best", "better", "between", "big", "certain", "change",
    "different", "end", "enough", "far", "few", "full", "further",
    "given", "good", "great", "high", "however", "important", "including",
    "keep", "kind", "large", "later", "less", "level", "likely", "look",
    "looking", "making", "many", "might", "next", "number", "often",
    "old", "order", "others", "part", "place", "point", "possible",
    "public", "real", "really", "several", "shall", "side", "since",
    "small", "something", "start", "started", "state", "through",
    "together", "toward", "upon", "using", "well", "without", "whole",
    "within", "yet", "above", "below", "near", "never", "always",
    # Generic job posting words
    "team", "role", "company", "business", "product", "products",
    "service", "services", "world", "people", "customers", "customer",
    "solutions", "platform", "opportunity", "opportunities",
    "responsible", "responsibilities", "ability", "strong", "experience",
    "working", "based", "related", "build", "building", "ensure",
    "create", "support", "manage", "develop", "developing", "design",
    "designing", "implement", "implementing", "understanding",
    "knowledge", "skills", "years", "year", "must", "required",
    "preferred", "plus", "bonus", "etc", "ideal", "key", "core",
    "use", "mission", "vision", "value", "values", "culture",
    "environment", "position", "candidate", "applicant", "apply",
    "application", "hiring", "hire", "interview", "salary", "pay",
    "compensation", "benefits", "benefit", "equity", "stock",
    "insurance", "vacation", "flexible", "office", "remote", "hybrid",
    "location", "based", "reports", "reporting", "collaborate",
    "collaboration", "communicate", "communication", "passionate",
    "driven", "motivated", "innovative", "excellence", "impact",
    "success", "successful", "organization", "organizational",
    "process", "processes", "project", "projects", "deliver",
    "delivery", "quality", "performance", "growth", "strategy",
    "strategic", "goal", "goals", "objective", "result", "results",
    "stakeholder", "stakeholders", "cross-functional", "partner",
    "partners", "partnership", "industry", "market", "global",
    "international", "domestic", "regional", "local",
    # Generic job title / description words (not tech-specific)
    "data", "engineer", "engineering", "developer", "software",
    "senior", "junior", "manager", "analyst", "specialist",
    "consultant", "coordinator", "administrator", "associate",
    "systems", "technical", "technology", "operations",
    "programming", "programs", "program", "information",
    "digital", "computer", "tools", "tool", "code", "coding",
    "production", "documentation", "requirements", "specifications",
    "features", "feature", "comfortable", "proficient",
    "proficiency", "familiarity", "hands-on", "practical",
    "deep", "solid", "extensive", "minimum", "least",
    "equivalent", "including", "across", "multiple", "various",
    # Power / action verbs (non-technical)
    "achieve", "achieved", "advance", "analyze", "approach",
    "assess", "assist", "attract", "believe", "believes",
    "capable", "champion", "coach", "contribute", "coordinate",
    "define", "demonstrate", "determine", "drive", "driving",
    "educate", "elevate", "embrace", "empower", "enable",
    "engage", "enhance", "establish", "evaluate", "evolve",
    "exceed", "execute", "expand", "explore", "facilitate",
    "foster", "guide", "identify", "improve", "influence",
    "inform", "initiate", "inspire", "integrate", "launch",
    "leverage", "maintain", "mentor", "monitor", "navigate",
    "optimize", "oversee", "own", "participate", "perform",
    "pioneer", "plan", "prioritize", "promote", "pursue",
    "recommend", "refine", "resolve", "review", "scale",
    "shape", "simplify", "solve", "streamline", "strengthen",
    "thrive", "track", "transform", "translate", "trust",
    "uncover", "validate", "ambitious", "beyond", "power",
    "financial", "pushing", "pushed", "capable", "increase",
    "freedom", "massive", "incredible", "like-minded",
})


def _strip_html(text: str) -> str:
    """Remove HTML tags and entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&\w+;", " ", text)
    return text


def _tokenize(text: str) -> set[str]:
    """Extract lowercase word tokens (2+ chars, allows +#.- for tech terms)."""
    clean = _strip_html(text.lower())
    return set(re.findall(r"[a-z][a-z0-9+#.\-]{1,30}", clean))


def _clean_token(t: str) -> str:
    """Strip trailing dots/punctuation from tokens (but keep internal ones like node.js)."""
    return t.rstrip(".,;:!?")


def _tech_tokens(tokens: set[str]) -> set[str]:
    """Filter tokens to likely tech/skill terms by removing stop words and short words."""
    cleaned = {_clean_token(t) for t in tokens}
    return {t for t in cleaned if t and t not in _STOP_WORDS and len(t) >= 3}


def _extract_years_required(text: str) -> int | None:
    """Extract the highest 'N+ years' number from job text."""
    matches = YEARS_PATTERN.findall(text)
    return max(int(m) for m in matches) if matches else None


def _estimate_seniority_years(text: str) -> int | None:
    """Estimate years from seniority keywords in title/description."""
    text_lower = text.lower()
    best: int | None = None
    for pattern, years in SENIORITY_SIGNALS.items():
        if re.search(pattern, text_lower):
            if best is None or years > best:
                best = years
    return best


class RuleBasedScorer:
    """Score jobs using a 4-component weighted system.

    Components (each scaled 0–100, then weighted):
      title_score      x 0.20  — role words in job title
      keyword_score    x 0.40  — must-have / nice-to-have / avoid
      experience_score x 0.20  — JD seniority vs. user's experience range
      techstack_score  x 0.20  — resume token overlap with JD
    """

    WEIGHT_TITLE = 0.20
    WEIGHT_KEYWORD = 0.40
    WEIGHT_EXPERIENCE = 0.20
    WEIGHT_TECHSTACK = 0.20

    def __init__(self, resume_texts: dict[str, str], search: SearchConfig) -> None:
        self.search = search
        self.role_resume_tokens: dict[str, set[str]] = {}
        self._all_resume_tokens: set[str] = set()
        self._load_resumes(resume_texts)

    def _load_resumes(self, resume_texts: dict[str, str]) -> None:
        for role, text in resume_texts.items():
            tokens = _tokenize(text)
            self.role_resume_tokens[role] = tokens
            self._all_resume_tokens |= tokens
            logger.info(f"Resume for '{role}': {len(tokens)} tokens")
        if not resume_texts:
            logger.warning("No resume texts provided — tech stack scoring will use defaults")

    # ── Component 1: Title match (0–100) ────────────────────

    def _score_title(self, job: Job) -> tuple[float, list[str]]:
        """Score how well searched roles match the job title."""
        title_lower = job.title.lower()
        matched_roles: list[str] = []

        for role in self.search.roles:
            role_lower = role.lower()
            if role_lower in title_lower:
                matched_roles.append(role)
                continue
            words = role_lower.split()
            hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", title_lower))
            if len(words) >= 2 and hits >= len(words) // 2 + 1:
                matched_roles.append(role)
            elif len(words) == 1 and hits == 1:
                matched_roles.append(role)

        if not matched_roles:
            return 0.0, matched_roles

        return min(100.0, 50.0 + len(matched_roles) * 25.0), matched_roles

    # ── Component 2: Keyword overlap (0–100) ────────────────

    def _score_keywords(self, job: Job) -> tuple[float, list[str]]:
        """Score based on must-have, nice-to-have, and avoid keywords.

        Returns (score, flags). The actual strengths/gaps come from
        _analyze_resume_vs_jd which compares resume tokens against JD.
        """
        jd_text = (job.description_full or job.description_snippet).lower()
        jd_tokens = _tokenize(jd_text)
        title_lower = job.title.lower()
        combined_tokens = jd_tokens | _tokenize(title_lower)

        score = 50.0
        flags: list[str] = []

        for kw in self.search.must_have:
            if kw in combined_tokens or kw in jd_text:
                score += 10
            else:
                score -= 8

        for kw in self.search.nice_to_have:
            if kw in combined_tokens or kw in jd_text:
                score += 4

        for kw in self.search.avoid:
            if kw in combined_tokens or kw in jd_text or kw in title_lower:
                score -= 15
                flags.append(f"avoid: {kw}")

        return max(0.0, min(100.0, score)), flags

    # ── Resume vs JD analysis (strengths & gaps) ─────────────

    def _analyze_resume_vs_jd(
        self, job: Job, resume_tokens: set[str],
    ) -> tuple[list[str], list[str]]:
        """Compare resume tokens against JD tokens to find strengths and gaps.

        Strategy:
          - Strengths = resume tech tokens found in JD (your skills they want)
          - Gaps = JD tech tokens NOT in resume, filtered to likely-tech terms
            (contains digits/special chars, or is a known programming/tool term)

        Returns:
            (strong_matches, missing_keywords)
        """
        jd_text = (job.description_full or job.description_snippet).lower()
        jd_tech = _tech_tokens(_tokenize(jd_text))
        resume_tech = _tech_tokens(resume_tokens)

        if not jd_tech:
            return [], []

        # Strengths: your resume skills that appear in the JD
        overlap = jd_tech & resume_tech
        strong = sorted(overlap)[:10]

        # Gaps: JD tech terms not in your resume
        # Extra filter: prefer tokens with digits/special chars (c++, s3, ec2)
        # or well-known short tech terms over generic English words
        jd_only = jd_tech - resume_tech
        # Prioritize tokens that look "techy" (contain digits, +, #, .)
        techy = sorted(t for t in jd_only if re.search(r"[0-9+#.]", t))
        # Then add remaining alpha-only tokens
        alpha = sorted(t for t in jd_only if not re.search(r"[0-9+#.]", t))
        gaps = (techy + alpha)[:8]

        strong_list = [f"{s} -- Resume + JD match" for s in strong]
        missing_list = [f"{g} -- In JD, not in resume" for g in gaps]

        return strong_list, missing_list

    # ── Component 3: Experience alignment (0–100) ────────────

    def _score_experience(self, job: Job) -> tuple[float, str, list[str]]:
        """Score based on experience range alignment.

        Returns experience_alignment in numeric format:
          "Required: 4 yrs | Yours: 0-3 yrs"
          "Required: Not specified | Yours: 0-3 yrs"
        """
        combined = job.title + " " + (job.description_full or job.description_snippet)
        flags: list[str] = []
        min_y, max_y = self.search.min_years, self.search.max_years
        yours = f"{min_y}-{max_y} yrs"

        years_req = _extract_years_required(combined)
        if years_req is None:
            years_req = _estimate_seniority_years(combined)

        if years_req is None:
            return 70.0, f"Required: Not specified | Yours: {yours}", flags

        req_str = f"{years_req} yrs"

        if min_y <= years_req <= max_y:
            return 100.0, f"Required: {req_str} | Yours: {yours}", flags

        if years_req < min_y:
            gap = min_y - years_req
            alignment = f"Required: {req_str} | Yours: {yours} (Overqualified)"
            flags.append(f"JD ~{years_req}yr, you have {min_y}-{max_y}yr")
            return max(30.0, 100.0 - gap * 15), alignment, flags

        # years_req > max_y
        gap = years_req - max_y
        label = "Above max" if gap <= 3 else "Way above max"
        alignment = f"Required: {req_str} | Yours: {yours} ({label})"
        flags.append(f"JD ~{years_req}yr, you have {min_y}-{max_y}yr")
        if gap > 5:
            return 10.0, alignment, flags
        return max(20.0, 100.0 - gap * 20), alignment, flags

    # ── Component 4: Tech stack overlap (0–100) ──────────────

    def _get_resume_tokens_for_job(self, job: Job) -> set[str]:
        """Get the best resume tokens for a job based on its matched roles.

        Uses the union of tokens from all roles matched by this job.
        Falls back to all available resume tokens if no role-specific match.
        """
        if not self.role_resume_tokens:
            return set()

        matched = job.roles_matched
        if matched:
            tokens: set[str] = set()
            for role in matched:
                if role in self.role_resume_tokens:
                    tokens |= self.role_resume_tokens[role]
            if tokens:
                return tokens

        return self._all_resume_tokens

    def _score_techstack(self, job: Job, resume_tokens: set[str]) -> float:
        """Score based on resume token overlap with job description."""
        if not resume_tokens:
            return 50.0

        jd_text = (job.description_full or job.description_snippet).lower()
        jd_tokens = _tokenize(jd_text)
        if not jd_tokens:
            return 50.0

        overlap = jd_tokens & resume_tokens
        ratio = len(overlap) / max(len(jd_tokens), 1)
        return min(100.0, 20.0 + ratio * 400)

    # ── Resume improvement prompt ─────────────────────────────

    @staticmethod
    def _generate_resume_prompt(job: Job, strong: list[str], missing: list[str]) -> str:
        """Build an AI-ready resume improvement prompt from scoring results."""
        lines: list[str] = []
        lines.append(f"Tailor my resume for: {job.title} at {job.company}.")

        strengths = [s.split(" -- ")[0] for s in strong]
        gaps = [m.split(" -- ")[0] for m in missing]

        if strengths:
            lines.append(f"Strengths to highlight: {', '.join(strengths)}.")
        if gaps:
            lines.append(f"Gaps to address: {', '.join(gaps)}.")
        if job.experience_alignment:
            lines.append(f"Experience: {job.experience_alignment}.")

        lines.append("Rewrite my summary and skills sections to maximize match.")
        return " ".join(lines)

    # ── Aggregate scoring ────────────────────────────────────

    def score(self, job: Job) -> Job:
        """Score a single job using 4 weighted components. Mutates and returns the job."""
        resume_tokens = self._get_resume_tokens_for_job(job)

        title_score, matched_roles = self._score_title(job)
        keyword_score, kw_flags = self._score_keywords(job)
        experience_score, exp_alignment, exp_flags = self._score_experience(job)
        techstack_score = self._score_techstack(job, resume_tokens)

        # Resume vs JD analysis — the core strengths/gaps
        strong, missing = self._analyze_resume_vs_jd(job, resume_tokens)

        final = (
            title_score * self.WEIGHT_TITLE
            + keyword_score * self.WEIGHT_KEYWORD
            + experience_score * self.WEIGHT_EXPERIENCE
            + techstack_score * self.WEIGHT_TECHSTACK
        )
        final = max(0, min(100, round(final)))

        all_flags = kw_flags + exp_flags

        logger.debug(
            f"[{job.company} | {job.title}] title={title_score:.0f} kw={keyword_score:.0f} "
            f"exp={experience_score:.0f} tech={techstack_score:.0f} → final={final}"
        )

        if "Way above max" in exp_alignment:
            rec = "Low Match - Skip"
        elif final >= 75:
            rec = "Apply Immediately"
        elif final >= 55:
            rec = "Apply With Tweaks"
        else:
            rec = "Low Match - Skip"

        job.match_score = final
        job.recommendation = rec
        job.strong_matches = strong
        job.missing_keywords = missing
        job.red_flags = all_flags
        job.experience_alignment = exp_alignment
        job.resume_improvement_prompt = self._generate_resume_prompt(job, strong, missing)
        if matched_roles:
            job.roles_matched = sorted(set(job.roles_matched) | set(matched_roles))

        return job

    def score_batch(self, jobs: list[Job]) -> list[Job]:
        """Score a list of jobs."""
        return [self.score(j) for j in jobs]
