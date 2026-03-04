"""Load configuration from config.yaml and .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class SearchConfig:
    """User-configurable search parameters (supports multiple roles)."""

    roles: list[str] = field(default_factory=lambda: ["Data Engineer"])
    min_years: int = 0
    max_years: int = 5
    location: str = ""
    remote_only: bool = False
    must_have: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    search: SearchConfig
    companies: dict[str, list[str]]
    resumes_dir: Path
    resume_path: Path  # legacy fallback
    db_path: Path
    excel_path: Path
    score_threshold: int
    max_results_per_run: int
    freshness_days: int
    fetch_interval_minutes: int
    llm_enabled: bool
    # db_backend defaults to "sqlite" so old cached configs don't crash
    db_backend: str = "sqlite"
    startup_portals: list[str] = field(default_factory=list)


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration from YAML + env vars."""
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"

    with open(config_path, "r") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    search_raw = raw.get("search", {})
    exp = search_raw.get("experience_range", {})
    kw = search_raw.get("keywords", {})

    # Support both single role_query (legacy) and roles list
    roles_raw = search_raw.get("roles", None)
    if roles_raw is None:
        legacy = search_raw.get("role_query", "Data Engineer")
        roles_raw = [legacy] if isinstance(legacy, str) else legacy
    elif isinstance(roles_raw, str):
        roles_raw = [roles_raw]

    search = SearchConfig(
        roles=[r.strip() for r in roles_raw if r.strip()],
        min_years=exp.get("min_years", 0),
        max_years=exp.get("max_years", 5),
        location=search_raw.get("location", ""),
        remote_only=search_raw.get("remote_only", False),
        must_have=[w.lower() for w in kw.get("must_have", [])],
        nice_to_have=[w.lower() for w in kw.get("nice_to_have", [])],
        avoid=[w.lower() for w in kw.get("avoid", [])],
    )

    companies = raw.get("companies", {})
    resume_rel = raw.get("resume_path", "resume.txt")
    resumes_dir_rel = raw.get("resumes_dir", "resumes")

    # DB backend: "supabase" if SUPABASE_URL is set, else "sqlite"
    db_backend = "supabase" if os.getenv("SUPABASE_URL") else "sqlite"

    # Startup portals (list of portal names like "remoteok", "yc", etc.)
    portals = raw.get("startup_portals", [])
    if not isinstance(portals, list):
        portals = []

    return AppConfig(
        search=search,
        companies=companies,
        resumes_dir=PROJECT_ROOT / resumes_dir_rel,
        resume_path=PROJECT_ROOT / resume_rel,
        db_backend=db_backend,
        db_path=PROJECT_ROOT / os.getenv("DB_PATH", "jobs.db"),
        excel_path=PROJECT_ROOT / os.getenv("EXCEL_PATH", "job_tracker.xlsx"),
        score_threshold=_env_int("SCORE_THRESHOLD", 65),
        max_results_per_run=_env_int("MAX_RESULTS_PER_RUN", 50),
        freshness_days=_env_int("FRESHNESS_DAYS", 3),
        fetch_interval_minutes=_env_int("FETCH_INTERVAL_MINUTES", 30),
        llm_enabled=_env_bool("LLM_ENABLED", False),
        startup_portals=portals,
    )


def get_db(cfg: AppConfig):
    """Create the appropriate database backend based on config.

    Uses getattr fallback so old cached AppConfig objects (e.g. from
    Streamlit's @st.cache_resource) that lack db_backend still work.
    """
    backend = getattr(cfg, "db_backend", "sqlite")
    if backend == "supabase":
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "db_backend is 'supabase' but SUPABASE_URL/SUPABASE_KEY are not set in .env"
            )
        from src.database_supabase import SupabaseJobDB
        return SupabaseJobDB()
    from src.database import JobDB
    return JobDB(cfg.db_path)
