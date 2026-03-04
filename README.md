# Job Tracker

Multi-role, resume-aware job aggregation app that fetches new postings from Greenhouse, Lever, and Ashby job boards, scores them against your resume using a 4-component weighted engine, and exports results to Excel. Runs locally on macOS with zero cost by default.

## Features

- **Multi-role search** — search for "Data Engineer", "Software Engineer", "AI Engineer" in one run
- Fetches from **Greenhouse**, **Lever**, and **Ashby** public APIs (34 companies preconfigured)
- **Startup portals** — WeWorkRemotely, Wellfound, YC
- **Auto-discovery** — finds new company boards from YC directory + custom company names
- **4-component scoring** — Title match (20%) + Keyword overlap (40%) + Experience alignment (20%) + Tech stack (20%)
- **Experience level detection** — detects Intern/Entry/Mid/Senior/Staff+ from job titles, filterable via sidebar
- **Visa sponsorship detection** — scans descriptions for sponsorship language
- **Cross-role deduplication** — one job matching multiple roles is stored once with all roles listed
- **Job lifecycle tracking** — Discovered → Applied → Interviewing → Offer / Rejected / Withdrawn / Archived
- **Daily application goal reminder** — email notification via Gmail if you haven't hit your target
- **Dual database** — SQLite (local) or Supabase (cloud)
- **Excel export** with hyperlinks, autofilter, frozen headers, and role tags
- **Streamlit dashboard** with filters, progress bar, and interactive table
- **CLI mode** for single runs or cron jobs
- **Background scheduler** — auto-fetches every 30 minutes
- **Pluggable LLM scoring** (off by default, zero cost)

## Quick Start (macOS)

### 1. Enter the project

```bash
cd ~/job-tracker
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `config.yaml` to set your roles, keywords, and companies:

```yaml
search:
  roles:
    - "Data Engineer"
    - "Software Engineer"
    - "AI Engineer"
  experience_range:
    min_years: 0
    max_years: 5
```

### 4. Add your resume

Place per-role PDF resumes in the `resumes/` folder:

```
resumes/
  hj_resume_data_engineer.pdf
  hj_resume_software_engineer.pdf
  hj_resume_ai_engineer.pdf
```

Or upload via the Streamlit sidebar.

### 5. Run

**Option A: Streamlit UI**
```bash
streamlit run app.py
```

**Option B: CLI (single run)**
```bash
python main.py --run-once
```

**Option C: CLI (continuous scheduler)**
```bash
python main.py --loop
```

**Option D: CLI with role override**
```bash
python main.py --run-once --roles "ML Engineer, Analytics Engineer"
```

## Daily Application Goal Reminder

Get an email at 9 PM if you haven't applied to enough jobs today.

### Setup

1. Add to your `.env`:
   ```
   NOTIFY_EMAIL=your.email@gmail.com
   GMAIL_APP_PASSWORD=your-app-password
   DAILY_GOAL=15
   NOTIFY_HOUR=21
   ```

2. Get a Gmail App Password: Google Account → Security → 2-Step Verification → App Passwords

3. Install the daily cron (runs even without the app open):
   ```bash
   cp com.jobtracker.dailygoal.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.jobtracker.dailygoal.plist
   ```

4. Test manually:
   ```bash
   python check_daily_goal.py
   ```

## Adding Roles

Edit `config.yaml`:

```yaml
search:
  roles:
    - "Data Engineer"
    - "ML Engineer"
    - "Analytics Engineer"
    - "Backend Engineer"
```

Or override via CLI:
```bash
python main.py --run-once --roles "ML Engineer, Backend Engineer"
```

Or type them comma-separated in the Streamlit sidebar.

## Adding Companies

Edit `config.yaml` under `companies`:

```yaml
companies:
  greenhouse:
    - stripe          # https://boards-api.greenhouse.io/v1/boards/stripe/jobs
    - your-company
  lever:
    - plaid           # https://api.lever.co/v0/postings/plaid
  ashby:
    - linear          # https://api.ashbyhq.com/posting-api/job-board/linear
```

Or use the **Discover Companies** button in the UI to auto-find company boards by name.

**Finding slugs:**
- **Greenhouse**: `https://boards.greenhouse.io/{slug}`
- **Lever**: `https://jobs.lever.co/{slug}`
- **Ashby**: `https://jobs.ashbyhq.com/{slug}`

## How Scoring Works

The scoring engine is **local and deterministic** (zero API calls, zero cost). Each job is scored using **4 weighted components**:

### Components

| Component | Weight | Description |
|-----------|--------|-------------|
| **Title Match** | 20% | Do any of your searched roles appear in the job title? |
| **Keyword Overlap** | 40% | Must-have (+10 each), nice-to-have (+4), avoid (-15) |
| **Experience Alignment** | 20% | Parses "N+ years", "Senior", "Staff" etc. vs your range |
| **Tech Stack Overlap** | 20% | Resume tokens vs. job description tokens |

Each component scores 0-100, then they're weighted to produce a final 0-100 score.

### Experience Level Detection

The engine detects seniority from job titles:

| Level | Keywords |
|-------|----------|
| Intern | intern, internship |
| Entry Level | junior, jr, entry-level, new grad, associate |
| Mid Level | mid-level |
| Senior | senior, sr |
| Staff+ | staff, lead, principal, director, vp, fellow |

Use the **Experience Levels** multiselect in the sidebar to show/hide levels.

### Recommendations

| Score | Recommendation |
|-------|---------------|
| >= 75 | **Apply Immediately** |
| >= 55 | **Apply With Tweaks** |
| < 55 | **Low Match - Skip** |

### Optional LLM Scoring

Set `LLM_ENABLED=true` in `.env` and provide an API key:

```env
LLM_ENABLED=true
ANTHROPIC_API_KEY=sk-ant-...
# or
OPENAI_API_KEY=sk-...
```

## Troubleshooting

### "No jobs found"
- Ensure your roles match actual job titles (e.g., "Data Engineer" not "DE")
- Increase `FRESHNESS_DAYS` if few jobs posted recently (default: 3)
- Lower `SCORE_THRESHOLD` to see more results (default: 65)

### "Connection error" for a company
- The company may have changed their board slug — verify URL in a browser
- The fetcher will log a warning and continue with other companies

### Streamlit won't start
```bash
pip install streamlit
```

### Duplicate jobs
- Dedup uses `(company + title + location)` as the key
- If a company reposts with a slightly different title, it appears as new

## Project Structure

```
job-tracker/
├── main.py                 # CLI entry point (--run-once / --loop)
├── app.py                  # Streamlit dashboard
├── config.yaml             # Roles, companies, keywords
├── check_daily_goal.py     # Standalone daily goal checker (for cron)
├── com.jobtracker.dailygoal.plist  # macOS launchd config
├── .env.example            # Environment variables template
├── requirements.txt
├── resumes/                # Per-role PDF resumes
│   └── hj_resume_data_engineer.pdf
├── supabase_schema.sql     # Supabase table definitions
├── src/
│   ├── config.py           # Multi-role config loader
│   ├── models.py           # Job model with lifecycle states
│   ├── database.py         # SQLite backend
│   ├── database_supabase.py # Supabase backend
│   ├── pipeline.py         # Fetch → filter → score → store pipeline
│   ├── dedup.py            # Cross-role dedup with role merging
│   ├── filters.py          # Relevance, experience, sponsorship, location
│   ├── relevance.py        # Resume keyword extraction
│   ├── resume_loader.py    # Per-role PDF resume loading
│   ├── exporter.py         # Excel export
│   ├── notifier.py         # Daily goal email reminder
│   ├── scheduler.py        # APScheduler background jobs
│   ├── slug_discovery.py   # Auto-discover company ATS boards
│   ├── fetchers/
│   │   ├── base.py         # Abstract base fetcher
│   │   ├── greenhouse.py   # Greenhouse API
│   │   ├── lever.py        # Lever API
│   │   ├── ashby.py        # Ashby API
│   │   └── startup/        # Portal fetchers (WWR, Wellfound, YC)
│   └── scoring/
│       ├── rule_based.py   # 4-component weighted scorer
│       └── llm_scoring.py  # Optional LLM scorer
└── .gitignore
```
