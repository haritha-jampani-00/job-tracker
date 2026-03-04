"""Optional LLM-based scoring module.

Only active when LLM_ENABLED=true. Provides richer analysis but costs money.
Implements the same interface as RuleBasedScorer so it's a drop-in replacement.
"""

from __future__ import annotations

import json
import logging
import os

from src.config import SearchConfig
from src.models import Job

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a career advisor. Analyze how well this job matches the candidate's resume.

## Resume
{resume}

## Job
Title: {title}
Company: {company}
Location: {location}
Description: {description}

## Candidate experience: {min_years}-{max_years} years
## Roles searched: {roles}
## Must-have skills: {must_have}
## Nice-to-have skills: {nice_to_have}
## Avoid keywords: {avoid}

Return ONLY valid JSON with these fields:
{{
  "match_score": <int 0-100>,
  "recommendation": "<Apply Immediately | Apply With Tweaks | Low Match - Skip>",
  "experience_alignment": "<Required: N yrs | Yours: {min_years}-{max_years} yrs> or <Required: Not specified | Yours: {min_years}-{max_years} yrs>",
  "strong_matches": ["skill -- Resume + JD match", "skill -- JD only"],
  "missing_keywords": ["skill -- Not mentioned in resume", "skill -- In JD, not in resume"],
  "red_flags": ["flag1"],
  "resume_improvement_prompt": "<A 2-3 sentence actionable prompt for rewriting the resume to match this job>"
}}"""


class LLMScorer:
    """LLM-based job scorer using Claude or OpenAI API."""

    def __init__(self, resume_texts: dict[str, str], search: SearchConfig) -> None:
        self.search = search
        self.resume_texts = resume_texts
        # Combined fallback text (truncated)
        self._combined_resume = "\n\n".join(resume_texts.values())[:3000] if resume_texts else ""

        self._provider = self._detect_provider()

    def _detect_provider(self) -> str | None:
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        logger.warning("LLM_ENABLED=true but no API key found. Falling back to rule-based.")
        return None

    def _get_resume_for_job(self, job: Job) -> str:
        """Get the best resume text for a job based on matched roles."""
        for role in job.roles_matched:
            if role in self.resume_texts:
                return self.resume_texts[role][:2000]
        return self._combined_resume[:2000]

    def _build_prompt(self, job: Job) -> str:
        return PROMPT_TEMPLATE.format(
            resume=self._get_resume_for_job(job),
            title=job.title,
            company=job.company,
            location=job.location,
            description=(job.description_full or job.description_snippet)[:2000],
            min_years=self.search.min_years,
            max_years=self.search.max_years,
            roles=", ".join(self.search.roles),
            must_have=", ".join(self.search.must_have),
            nice_to_have=", ".join(self.search.nice_to_have),
            avoid=", ".join(self.search.avoid),
        )

    def _call_anthropic(self, prompt: str) -> dict:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(msg.content[0].text)

    def _call_openai(self, prompt: str) -> dict:
        import openai
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        return json.loads(resp.choices[0].message.content)

    def score(self, job: Job) -> Job:
        if not self._provider:
            return job

        prompt = self._build_prompt(job)
        try:
            if self._provider == "anthropic":
                result = self._call_anthropic(prompt)
            else:
                result = self._call_openai(prompt)

            job.match_score = result.get("match_score", 50)
            job.recommendation = result.get("recommendation", "Apply with tweaks")
            job.strong_matches = result.get("strong_matches", [])
            job.missing_keywords = result.get("missing_keywords", [])
            job.red_flags = result.get("red_flags", [])
            job.experience_alignment = result.get("experience_alignment", "")
            job.resume_improvement_prompt = result.get("resume_improvement_prompt", "")
        except Exception as e:
            logger.error(f"LLM scoring failed for {job.title}: {e}")

        return job

    def score_batch(self, jobs: list[Job]) -> list[Job]:
        return [self.score(j) for j in jobs]
