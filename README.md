# Job Tracker

Multi-role, resume-aware job aggregation app that fetches new postings from Greenhouse, Lever, and Ashby job boards, scores them against your resume using a 4-component weighted engine, and exports results to Excel. Runs locally on macOS with zero cost by default.

## Features

- **Multi-role search** — search for "Data Engineer", "Software Engineer", "AI Engineer" in one run
- Fetches from **Greenhouse**, **Lever**, and **Ashby** public APIs (34 companies preconfigured)
- **4-component scoring** — Title match (20%) + Keyword overlap (40%) + Experience alignment (20%) + Tech stack (20%)
- **Experience awareness** — detects seniority signals and aligns with your experience range
- **Cross-role deduplication** — one job matching multiple roles is stored once with all roles listed
- **Excel export** with hyperlinks, autofilter, frozen headers, and role tags
- **Streamlit dashboard** with multi-role input, role filtering, and download
- **CLI mode** for single runs or cron jobs
- **Background scheduler** — auto-fetches every 30 minutes
- **Pluggable LLM scoring** (off by default, zero cost)

## Quick Start (macOS M2)

### 1. Enter the project

```bash
cd ~/job-tracker
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure

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

### 5. Add your resume

Replace `resume.txt` with your actual resume in plain text.

### 6. Run

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

### 7. Optional: macOS cron job

```bash
crontab -e
```

Add (adjust paths):
```
*/30 * * * * cd ~/job-tracker && .venv/bin/python main.py --run-once >> cron.log 2>&1
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

### Experience Alignment

The engine detects seniority signals in titles and descriptions:

| Signal | Estimated Years |
|--------|----------------|
| Intern, Junior, Entry-level | 0 |
| Mid-level | 3 |
| Senior | 5 |
| Lead | 7 |
| Staff | 8 |
| Principal | 10 |
| Director, VP | 12-15 |

If the JD suggests seniority beyond your `max_years + 5`, the job is flagged as "Way above range" and recommended to skip.

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
source .venv/bin/activate
pip install streamlit
```

### Duplicate jobs
- Dedup uses `(company + title + location)` as the key
- If a company reposts with a slightly different title, it appears as new

### Cron not running
- Use full paths in crontab
- Check `cron.log` for errors

## Project Structure

```
job-tracker/
├── main.py                 # CLI entry point (--run-once / --loop)
├── app.py                  # Streamlit dashboard (multi-role)
├── config.yaml             # Roles, companies, keywords
├── .env.example            # Environment variables
├── requirements.txt
├── resume.txt              # Your resume (plain text)
├── src/
│   ├── config.py           # Multi-role config loader
│   ├── models.py           # Job model (roles_matched, experience_alignment)
│   ├── database.py         # SQLite with cross-role schema
│   ├── pipeline.py         # Multi-role fetch-score-store pipeline
│   ├── dedup.py            # Cross-role dedup with role merging
│   ├── filters.py          # Relevance, location, freshness, score
│   ├── exporter.py         # Excel with Roles Matched column
│   ├── scheduler.py        # APScheduler 30-min background
│   ├── fetchers/
│   │   ├── base.py         # Abstract base fetcher
│   │   ├── greenhouse.py   # Greenhouse API
│   │   ├── lever.py        # Lever API
│   │   └── ashby.py        # Ashby API
│   └── scoring/
│       ├── rule_based.py   # 4-component weighted scorer
│       └── llm_scoring.py  # Optional LLM scorer
└── .gitignore
```
