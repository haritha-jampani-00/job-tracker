"""Streamlit UI for the Job Tracker (multi-role support)."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import AppConfig, SearchConfig, get_db, load_config
from src.exporter import export_to_excel
from src.filters import EXPERIENCE_LEVELS
from src.models import JOB_LIFECYCLE_STATES
from src.pipeline import apply_filters_only, recompute_all, run_discovery, run_pipeline
from src.resume_loader import get_resume_status, normalize_role
from src.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

st.set_page_config(page_title="Job Tracker", page_icon="💼", layout="wide")

# ── Settings persistence ──────────────────────────────────
_SETTINGS_PATH = Path(__file__).resolve().parent / "ui_settings.json"


def _load_ui_settings() -> dict:
    if _SETTINGS_PATH.exists():
        try:
            with open(_SETTINGS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_ui_settings(settings: dict) -> None:
    with open(_SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


# ── Config ────────────────────────────────────────────────

@st.cache_resource
def get_config() -> AppConfig:
    return load_config()


def _parse_json(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _truncate_list(items: list, max_items: int = 3) -> str:
    """Join list items, showing at most max_items with a '+N more' suffix."""
    if not items:
        return ""
    shown = items[:max_items]
    rest = len(items) - max_items
    text = ", ".join(str(s) for s in shown)
    if rest > 0:
        text += f" +{rest} more"
    return text


def _truncate_text(text: str, max_len: int = 120) -> str:
    """Truncate text with ellipsis."""
    if not text or len(text) <= max_len:
        return text or ""
    return text[:max_len] + "..."


# ── Session state helpers ─────────────────────────────────

def _load_jobs_from_db(
    cfg: AppConfig,
    role_filter: str | None = None,
    status_filter: list[str] | None = None,
) -> list[dict]:
    """Fetch all jobs from DB and store in session_state."""
    db = get_db(cfg)
    try:
        return db.get_all_jobs(role_filter=role_filter, status_filter=status_filter)
    finally:
        db.close()


def _split_jobs(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split jobs into (visible, filtered) based on is_visible flag."""
    visible = [j for j in jobs if j.get("is_visible", True) in (True, 1)]
    filtered = [j for j in jobs if j.get("is_visible", True) in (False, 0)]
    return visible, filtered


def _refresh_state(
    cfg: AppConfig,
    role_filter: str | None = None,
    status_filter: list[str] | None = None,
) -> None:
    """Reload jobs from DB and recompute visible/filtered splits."""
    all_jobs = _load_jobs_from_db(cfg, role_filter=role_filter, status_filter=status_filter)
    visible, filtered = _split_jobs(all_jobs)
    st.session_state["all_jobs"] = all_jobs
    st.session_state["visible_jobs"] = visible
    st.session_state["filtered_jobs"] = filtered


def _get_last_run(cfg: AppConfig) -> dict | None:
    db = get_db(cfg)
    try:
        return db.get_last_run()
    finally:
        db.close()


# ── Sidebar ──────────────────────────────────────────────
cfg = get_config()
_saved = _load_ui_settings()

st.sidebar.title("Job Tracker")
st.sidebar.markdown("---")

st.sidebar.subheader("Roles (comma-separated)")
roles_input = st.sidebar.text_area(
    "Search Roles",
    value=_saved.get("roles", ", ".join(cfg.search.roles)),
    help="Enter multiple roles separated by commas",
)

st.sidebar.subheader("Experience")
col_min, col_max = st.sidebar.columns(2)
min_years = col_min.number_input("Min Years", value=_saved.get("min_years", cfg.search.min_years), min_value=0)
max_years = col_max.number_input("Max Years", value=_saved.get("max_years", cfg.search.max_years), min_value=0)

_default_exp_levels = _saved.get("allowed_experience_levels", [lv for lv in EXPERIENCE_LEVELS if lv != "Intern"])
allowed_experience_levels = st.sidebar.multiselect(
    "Experience Levels",
    options=EXPERIENCE_LEVELS,
    default=[lv for lv in _default_exp_levels if lv in EXPERIENCE_LEVELS],
    help="Show jobs at these experience levels (unknown-level jobs always shown)",
)

location = st.sidebar.text_input("Location (optional)", value=_saved.get("location", cfg.search.location))
remote_only = st.sidebar.checkbox("Remote Only", value=_saved.get("remote_only", cfg.search.remote_only))

st.sidebar.subheader("Keywords")
must_have = st.sidebar.text_area("Must Have (comma-separated)", value=_saved.get("must_have", ", ".join(cfg.search.must_have)))
nice_to_have = st.sidebar.text_area("Nice to Have (comma-separated)", value=_saved.get("nice_to_have", ", ".join(cfg.search.nice_to_have)))
avoid = st.sidebar.text_area("Avoid (comma-separated)", value=_saved.get("avoid", ", ".join(cfg.search.avoid)))

st.sidebar.subheader("Filters")
score_threshold = st.sidebar.slider("Min Match Score", 0, 100, _saved.get("score_threshold", cfg.score_threshold))
freshness_days = st.sidebar.slider("Freshness (days)", 1, 30, _saved.get("freshness_days", cfg.freshness_days))
max_results = st.sidebar.slider("Max Results per Run", 10, 200, _saved.get("max_results", cfg.max_results_per_run))
require_sponsorship = st.sidebar.checkbox(
    "Require Visa Sponsorship",
    value=_saved.get("require_sponsorship", True),
    help="Hide jobs that explicitly state no visa sponsorship",
)

st.sidebar.subheader("Status Filter")
_default_statuses = _saved.get("status_filter", ["Discovered", "Applied", "Interviewing"])
status_filter_selection = st.sidebar.multiselect(
    "Show jobs with status",
    options=JOB_LIFECYCLE_STATES,
    default=[s for s in _default_statuses if s in JOB_LIFECYCLE_STATES],
    help="Select which lifecycle statuses to display",
)

# ── Sidebar: Daily Goal Reminder ──────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("Daily Goal Reminder")
_notify_email = os.getenv("NOTIFY_EMAIL", "")
if _notify_email:
    st.sidebar.success(f"Email: {_notify_email}")
else:
    st.sidebar.warning("Set NOTIFY_EMAIL and GMAIL_APP_PASSWORD in .env to enable")
daily_goal = st.sidebar.number_input(
    "Daily application goal",
    min_value=1, max_value=100,
    value=int(os.getenv("DAILY_GOAL", "15")),
    help="Get an email reminder at 9 PM if you haven't applied to this many jobs today",
)
# Show today's progress
_db_for_count = get_db(cfg)
try:
    _today_count = _db_for_count.count_applications_today()
finally:
    _db_for_count.close()
_progress = min(_today_count / max(daily_goal, 1), 1.0)
st.sidebar.progress(_progress, text=f"Today: {_today_count}/{daily_goal} applied")

# ── Sidebar: Apply Filters button ────────────────────────
st.sidebar.markdown("---")
apply_filters_clicked = st.sidebar.button("Apply Filters", use_container_width=True)

# ── Sidebar: Resume management ────────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("Resumes (per-role PDFs)")
_sidebar_roles = [r.strip() for r in roles_input.split(",") if r.strip()]
_resume_status = get_resume_status(cfg.resumes_dir, _sidebar_roles)

for _role, _info in _resume_status.items():
    _norm = normalize_role(_role)
    if _info["source"] == "role_pdf":
        st.sidebar.success(f"{_role}: hj_resume_{_norm}.pdf")
    elif _info["source"] == "generic_pdf":
        _generic_name = Path(_info["path"]).name
        st.sidebar.info(f"{_role}: using {_generic_name}")
    else:
        st.sidebar.warning(f"{_role}: missing hj_resume_{_norm}.pdf")

st.sidebar.markdown("**Upload resume PDF for a role:**")
upload_role = st.sidebar.selectbox("Role", _sidebar_roles, key="upload_role") if _sidebar_roles else None
uploaded_pdf = st.sidebar.file_uploader("Upload PDF", type=["pdf"], key="resume_pdf")
if uploaded_pdf and upload_role:
    cfg.resumes_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.resumes_dir / f"hj_resume_{normalize_role(upload_role)}.pdf"
    dest.write_bytes(uploaded_pdf.read())
    st.sidebar.success(f"Saved to {dest.name}")
    st.rerun()


# ── Build helpers ─────────────────────────────────────────

def _current_ui_settings() -> dict:
    return {
        "roles": roles_input,
        "min_years": min_years,
        "max_years": max_years,
        "location": location,
        "remote_only": remote_only,
        "must_have": must_have,
        "nice_to_have": nice_to_have,
        "avoid": avoid,
        "score_threshold": score_threshold,
        "freshness_days": freshness_days,
        "max_results": max_results,
        "require_sponsorship": require_sponsorship,
        "allowed_experience_levels": allowed_experience_levels,
        "status_filter": status_filter_selection,
    }


def build_search() -> SearchConfig:
    roles = [r.strip() for r in roles_input.split(",") if r.strip()]
    return SearchConfig(
        roles=roles if roles else ["Data Engineer"],
        min_years=min_years,
        max_years=max_years,
        location=location,
        remote_only=remote_only,
        must_have=[k.strip().lower() for k in must_have.split(",") if k.strip()],
        nice_to_have=[k.strip().lower() for k in nice_to_have.split(",") if k.strip()],
        avoid=[k.strip().lower() for k in avoid.split(",") if k.strip()],
    )


def _sync_cfg():
    """Push sidebar filter values to the cached config object."""
    cfg.score_threshold = score_threshold
    cfg.freshness_days = freshness_days
    cfg.max_results_per_run = max_results


# ── Main area ────────────────────────────────────────────
st.title("Job Tracker Dashboard")

roles_list = [r.strip() for r in roles_input.split(",") if r.strip()]
st.caption(f"Searching: {', '.join(roles_list)}")

# ── Action buttons ────────────────────────────────────────
st.markdown("---")
col_a, col_b, col_c, col_d, col_e = st.columns(5)

with col_a:
    fetch_clicked = st.button("Fetch Jobs", type="primary", use_container_width=True)

with col_b:
    recompute_clicked = st.button("Recompute Scores", use_container_width=True)

with col_c:
    export_clicked = st.button("Export Excel", use_container_width=True)

with col_d:
    discover_clicked = st.button("Discover Companies", use_container_width=True)

with col_e:
    scheduler_clicked = st.button("Start Auto-Fetch", use_container_width=True)


# ── Handle Fetch ──────────────────────────────────────────
if fetch_clicked:
    _save_ui_settings(_current_ui_settings())
    _sync_cfg()
    search = build_search()

    with st.spinner(f"Fetching jobs for {len(search.roles)} role(s)..."):
        try:
            result = run_pipeline(cfg, search_override=search, require_sponsorship=require_sponsorship, allowed_experience_levels=allowed_experience_levels)
            st.session_state["last_action"] = (
                f"Fetched: {result['jobs_found']} found, "
                f"{result['jobs_added']} new, "
                f"{result['jobs_exported']} total exported."
            )
            st.session_state["last_action_type"] = "success"
            # Store debug info for display
            st.session_state["pipeline_debug"] = result.get("debug", {})
            # Force reload from DB
            st.session_state.pop("all_jobs", None)
            st.rerun()
        except Exception as e:
            st.error(f"Pipeline failed: {e}")

# ── Handle Recompute ──────────────────────────────────────
if recompute_clicked:
    _save_ui_settings(_current_ui_settings())
    _sync_cfg()
    search = build_search()

    with st.spinner("Re-scoring all jobs with current settings..."):
        try:
            result = recompute_all(cfg, search_override=search, require_sponsorship=require_sponsorship, allowed_experience_levels=allowed_experience_levels)
            st.session_state["last_action"] = (
                f"Recomputed {result['total']} jobs: "
                f"{result['visible']} visible, {result['hidden']} filtered out."
            )
            st.session_state["last_action_type"] = "success"
            st.session_state.pop("all_jobs", None)
            st.rerun()
        except Exception as e:
            st.error(f"Recompute failed: {e}")

# ── Handle Apply Filters ─────────────────────────────────
if apply_filters_clicked:
    _save_ui_settings(_current_ui_settings())
    _sync_cfg()
    search = build_search()

    with st.spinner("Applying filters..."):
        try:
            result = apply_filters_only(cfg, search_override=search, require_sponsorship=require_sponsorship, allowed_experience_levels=allowed_experience_levels)
            st.session_state["last_action"] = (
                f"Filters applied: {result['visible']} visible, "
                f"{result['hidden']} filtered out of {result['total']} total."
            )
            st.session_state["last_action_type"] = "success"
            st.session_state.pop("all_jobs", None)
            st.rerun()
        except Exception as e:
            st.error(f"Apply filters failed: {e}")

# ── Handle Discover ───────────────────────────────────────
with st.expander("Discover New Companies", expanded=discover_clicked):
    custom_companies_input = st.text_area(
        "Company names to probe",
        placeholder="Enter company names (one per line or comma-separated)\ne.g. Stripe, Notion, Figma",
        help="Enter company names to check for Greenhouse/Lever/Ashby job boards. "
             "YC directory companies are also probed automatically.",
        height=100,
        key="custom_companies",
    )
    custom_companies: list[str] = []
    if custom_companies_input.strip():
        for line in custom_companies_input.replace(",", "\n").split("\n"):
            name = line.strip()
            if name:
                custom_companies.append(name)

if discover_clicked:
    with st.spinner("Discovering new company boards..."):
        try:
            result = run_discovery(cfg, custom_companies=custom_companies or None)
            parts = [
                f"Discovery complete: {result['total_new']} new boards found",
                f"(GH: {result['greenhouse']}, Lever: {result['lever']}, "
                f"Ashby: {result['ashby']})",
            ]
            if result.get("custom_companies"):
                parts.append(f"from {result['custom_companies']} custom + {result['company_names'] - result['custom_companies']} YC companies")
            else:
                parts.append(f"from {result['company_names']} YC companies")
            st.session_state["last_action"] = " ".join(parts)
            st.session_state["last_action_type"] = "success"
            st.rerun()
        except Exception as e:
            st.error(f"Discovery failed: {e}")

# ── Handle Export ─────────────────────────────────────────
if export_clicked:
    db = get_db(cfg)
    try:
        all_jobs = db.get_all_jobs()
    finally:
        db.close()
    if all_jobs:
        path = export_to_excel(all_jobs, cfg.excel_path)
        st.success(f"Exported {len(all_jobs)} jobs")
        with open(path, "rb") as f:
            st.download_button(
                label="Download Excel",
                data=f.read(),
                file_name="job_tracker.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.warning("No jobs to export.")

# ── Handle Scheduler ──────────────────────────────────────
if scheduler_clicked:
    start_scheduler(cfg)
    st.success(f"Scheduler started! Fetching every {cfg.fetch_interval_minutes} min.")

# ── Show last action message ──────────────────────────────
if "last_action" in st.session_state:
    action_type = st.session_state.pop("last_action_type", "info")
    msg = st.session_state.pop("last_action")
    if action_type == "success":
        st.success(msg)
    else:
        st.info(msg)

# ── Role filter for table ─────────────────────────────────
st.markdown("---")
filter_col1, filter_col2 = st.columns([3, 1])
with filter_col1:
    filter_options = ["All Roles"] + roles_list
    role_filter = st.selectbox("Filter by role", filter_options)
with filter_col2:
    show_filtered = st.checkbox("Show filtered-out jobs", value=False)

active_filter = None if role_filter == "All Roles" else role_filter

# ── Load data into session_state ──────────────────────────
# Reload from DB if not cached or if filters changed
_active_statuses = status_filter_selection if status_filter_selection else None
_cache_key = f"role_{active_filter}_status_{_active_statuses}"
if "all_jobs" not in st.session_state or st.session_state.get("_cache_key") != _cache_key:
    _refresh_state(cfg, role_filter=active_filter, status_filter=_active_statuses)
    st.session_state["_cache_key"] = _cache_key

all_jobs = st.session_state["all_jobs"]
visible_jobs = st.session_state["visible_jobs"]
filtered_jobs = st.session_state["filtered_jobs"]

# ── Metrics row ───────────────────────────────────────────
last_run = _get_last_run(cfg)


def _format_last_fetch(run_time_str: str) -> str:
    """Convert UTC run_time to local timezone with relative 'ago' label."""
    try:
        # Parse the UTC timestamp from DB
        utc_dt = datetime.fromisoformat(run_time_str).replace(tzinfo=timezone.utc)
        local_dt = utc_dt.astimezone()  # converts to system local timezone
        # Relative time
        delta = datetime.now(timezone.utc) - utc_dt
        total_min = int(delta.total_seconds() / 60)
        if total_min < 1:
            ago = "just now"
        elif total_min < 60:
            ago = f"{total_min}m ago"
        elif total_min < 1440:
            ago = f"{total_min // 60}h {total_min % 60}m ago"
        else:
            ago = f"{total_min // 1440}d ago"
        return ago
    except (ValueError, TypeError):
        return run_time_str[:16]


m1, m2, m3, m4 = st.columns(4)
m1.metric("Total in DB", len(all_jobs))
m2.metric("Visible", len(visible_jobs))
m3.metric("Filtered Out", len(filtered_jobs))
if last_run:
    m4.metric("Last Fetch", _format_last_fetch(last_run["run_time"]))
else:
    m4.metric("Last Fetch", "Never")

# ── Debug Info (expandable) ───────────────────────────────
if "pipeline_debug" in st.session_state and st.session_state["pipeline_debug"]:
    with st.expander("Fetch Summary (per source)", expanded=False):
        debug = st.session_state["pipeline_debug"]
        # Collect all source names across stages
        all_sources = sorted(
            set().union(*(d.keys() for d in debug.values() if isinstance(d, dict)))
        )
        if all_sources:
            # Build summary table
            import pandas as pd

            rows = []
            for source in all_sources:
                rows.append({
                    "Source": source,
                    "Fetched": debug.get("fetched", {}).get(source, 0),
                    "Relevant": debug.get("relevant", {}).get(source, 0),
                    "Fresh": debug.get("fresh", {}).get(source, 0),
                    "Stored": debug.get("stored", {}).get(source, 0),
                })
            # Add totals row
            rows.append({
                "Source": "TOTAL",
                "Fetched": sum(r["Fetched"] for r in rows),
                "Relevant": sum(r["Relevant"] for r in rows),
                "Fresh": sum(r["Fresh"] for r in rows),
                "Stored": sum(r["Stored"] for r in rows),
            })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.write("No source data available.")

# ── Job table ─────────────────────────────────────────────

STATUS_OPTIONS = JOB_LIFECYCLE_STATES

display_jobs = all_jobs if show_filtered else visible_jobs

if display_jobs:
    import pandas as pd

    display_data = []
    job_ids = []
    for j in display_jobs:
        strong = _parse_json(j.get("strong_matches", "[]"))
        missing = _parse_json(j.get("missing_keywords", "[]"))
        roles_m = _parse_json(j.get("roles_matched", "[]"))

        # Map sponsorship_status to display label
        _spons_raw = j.get("sponsorship_status", "unknown")
        _spons_label = {"sponsored": "Yes", "not_sponsored": "No"}.get(_spons_raw, "Unknown")

        job_ids.append(j["id"])
        row_data = {
            "Score": j["match_score"],
            "Status": j.get("job_status", "Discovered"),
            "Link": j["url"],
            "Roles": ", ".join(roles_m),
            "Company": j["company"],
            "Title": j["title"],
            "Location": j["location"],
            "Source": j["source"],
            "Rec.": j["recommendation"],
            "Exp. Level": j.get("experience_level", "") or "—",
            "Experience": j.get("experience_alignment", ""),
            "Sponsorship": _spons_label,
            "Top Strengths": ", ".join(str(s) for s in strong),
            "Gaps": ", ".join(str(s) for s in missing),
            "Resume Action Plan": j.get("resume_improvement_prompt", ""),
            "Notes": j.get("notes", ""),
            "Found": j["date_found"][:10] if j.get("date_found") else "",
        }
        if show_filtered:
            is_vis = j.get("is_visible", True) in (True, 1)
            row_data["Visible"] = "Yes" if is_vis else "No"
            row_data["Filter Reason"] = j.get("filter_reason", "")
        display_data.append(row_data)

    df = pd.DataFrame(display_data)

    disabled_cols = ["Score", "Roles", "Company", "Title", "Location", "Source",
                     "Rec.", "Exp. Level", "Experience", "Sponsorship", "Top Strengths",
                     "Gaps", "Resume Action Plan", "Link", "Found"]
    col_config = {
        "Score": st.column_config.NumberColumn("Score", width="small"),
        "Status": st.column_config.SelectboxColumn("Status", options=STATUS_OPTIONS, width="medium"),
        "Exp. Level": st.column_config.TextColumn("Exp. Level", width="small"),
        "Experience": st.column_config.TextColumn("Experience", width="medium"),
        "Sponsorship": st.column_config.TextColumn("Sponsorship", width="small"),
        "Top Strengths": st.column_config.TextColumn("Top Strengths", width="large"),
        "Gaps": st.column_config.TextColumn("Gaps", width="large"),
        "Resume Action Plan": st.column_config.TextColumn("Resume Action Plan", width="large"),
        "Notes": st.column_config.TextColumn("Notes", width="medium"),
        "Link": st.column_config.LinkColumn("Apply", width="medium"),
    }
    if show_filtered:
        disabled_cols += ["Visible", "Filter Reason"]

    edited = st.data_editor(
        df,
        use_container_width=True,
        height=600,
        num_rows="fixed",
        disabled=disabled_cols,
        column_config=col_config,
        key="job_table",
    )

    # Save status/notes changes back to DB
    db = get_db(cfg)
    try:
        for i, row in edited.iterrows():
            original = display_data[i]
            status_changed = row["Status"] != original["Status"]
            notes_changed = row["Notes"] != original["Notes"]
            if status_changed:
                db.update_job_status(job_ids[i], row["Status"])
            if notes_changed:
                db.update_status(job_ids[i], row["Status"], row["Notes"])
    finally:
        db.close()

    st.caption(
        f"Showing {len(display_jobs)} jobs "
        f"({len(visible_jobs)} visible, {len(filtered_jobs)} filtered out) "
        f"— edit Status and Notes directly in the table"
    )
else:
    st.info("No jobs found yet. Click 'Fetch Jobs' to start.")
