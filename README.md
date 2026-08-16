# JobScout AI

A local job search assistant. You upload your resume, JobScout AI pulls live postings from
Workday career sites plus JSearch and Adzuna, uses Gemini to score how well each one matches
you, and shows you the results in a Streamlit app on your own machine.

There is no hosted/public version of this app by design. It runs entirely on your computer,
using your own API keys, and your resume and search history stay in a local SQLite database
that never leaves your machine.

## What you'll need

- **Python 3.10 or newer** and `pip`
- **Git**, to clone the repo
- Four free/low-cost API keys (instructions below):
  - A **Gemini API key** (Google AI Studio) — used to extract structured data from your resume
    and to score job matches.
  - A **JSearch API key** (RapidAPI) — one of the two live job-posting sources.
  - An **Adzuna App ID and App Key** (Adzuna Developer) — the other live job-posting source.

None of these cost anything to obtain, and all four have free usage tiers that are more than
enough for personal use.

## 1. Clone the repo

```bash
git clone https://github.com/shaad459/jobfinder-ai.git
cd jobfinder-ai
```

## 2. Create a virtual environment (recommended)

```bash
python -m venv venv
```

Activate it:

- **Windows (PowerShell):** `venv\Scripts\Activate.ps1`
- **macOS / Linux:** `source venv/bin/activate`

You'll need to activate this every time you open a new terminal to run the app.

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

This installs Streamlit and everything the core app needs. (There's a second
`app/requirements.txt` with a couple of extra packages — `fastapi`, `uvicorn`,
`python-multipart` — only needed if you plan to run the optional REST API server described
under "Other ways to run this" below; you can skip it for normal use.)

## 4. Get your API keys

**Gemini API key**
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in with a
   Google account.
2. Click "Create API key" and copy it.

**JSearch API key**
1. Go to [rapidapi.com](https://rapidapi.com) and create a free account.
2. Search for and subscribe to the **JSearch** API (it has a free tier).
3. On the JSearch API page, copy your RapidAPI key (this is the same key used across all
   RapidAPI subscriptions on your account, not JSearch-specific).

**Adzuna App ID and App Key**
1. Go to [developer.adzuna.com](https://developer.adzuna.com) and register for a free account.
2. Once approved, your dashboard shows an **App ID** and **App Key** — you need both.

## 5. Configure your `.env` file

In the repo root, copy the example file:

```bash
cp .env.example .env
```

(Windows: `copy .env.example .env`)

Open `.env` in a text editor and fill in the four values you just collected:

```
GEMINI_API_KEY=your-key-here
JSEARCH_API_KEY=your-key-here
ADZUNA_APP_ID=your-app-id-here
ADZUNA_APP_KEY=your-app-key-here
```

`.env` is already listed in `.gitignore`, so it will never be committed or pushed to GitHub.
Never share this file or paste its contents anywhere public.

## 6. Run the app

From the repo root, with your virtual environment activated:

```bash
streamlit run app/streamlit_app.py
```

Streamlit will print a local URL (usually `http://localhost:8501`) and should open it in your
browser automatically. If it doesn't, open that URL yourself.

**First run:** the app automatically creates a local SQLite database
(`app/jobfinder.db`) the first time it starts — there's no separate database setup step. It
also pre-populates 5 example companies (Mastercard, Barclays, Deutsche Bank, Apex Group, Citi)
so there's something to search against immediately; you can remove or add to these from the
app's "Manage companies" section.

## Using the app

1. **Upload a resume** (.pdf or .docx) under "Resume library." JobScout AI extracts a
   structured profile from it using Gemini the first time (this is cached, so re-uploading the
   same file later is instant). You can upload more than one resume — each is saved
   permanently and can be searched against independently or together.
2. **Pick which resume(s) to search as** in the "Search as" multiselect. A job that matches
   more than one selected resume shows a separate score for each.
3. **Choose what to search**: check "Search all configured companies" to sweep every company
   in your list, or type a single company name. You can optionally narrow by role title and
   location, or check "I'm open to relocating" to ignore location filtering.
4. Click **Search**. JobScout AI reads from a shared job cache first (refreshed roughly every
   12 hours) for speed, falling back to a live API call for anything not cached; check
   "Search live instead of using the cached jobs" to force a fresh live search every time.
5. Results show a match tier (Strong / Good / Partial / None) with reasoning per resume.
   You can mark jobs as opened, export a PDF match report, or generate a resume tailored to a
   specific posting.

Adding a new company to search against (under "Manage companies") just needs that company's
Workday careers URL — JobScout AI parses the company/datacenter/site identifiers out of it
automatically, so you don't need to know Workday's internal naming scheme.

## Other ways to run this (optional)

The Streamlit app above is the main, all-in-one way to use JobScout AI — everything below is
optional.

### Web frontend (React + FastAPI)

There's a second, in-progress UI: a React frontend (`frontend/`) talking to a FastAPI backend
(`app/api_server.py`) instead of Streamlit. Both UIs share the exact same underlying matching
code and the same local database, so results are identical either way — this is just an
alternative interface. It needs two things running at once, in two separate terminals, and the
same `.env` file from setup step 5 above.

**Terminal 1 — the API backend** (from the repo root, with your virtual environment active):

```bash
pip install -r app/requirements.txt
cd app
uvicorn api_server:app --reload --port 8000
```

**Terminal 2 — the React frontend** (needs [Node.js](https://nodejs.org) installed separately):

```bash
cd frontend
npm install
npm run dev
```

This opens the frontend at `http://localhost:5173`, which talks to the API server at
`http://localhost:8000` (CORS on the backend is pre-configured for exactly this port, so no
extra setup is needed). Like the rest of JobScout AI, this is a local, single-user setup with
no authentication layer — don't deploy `api_server.py` to a public host as-is.

### Terminal chat assistant

`app/chat_assistant.py` is a free-text conversational interface to the same
search/match/export/tailor pipeline, run from a terminal instead of a browser:

```bash
python app/chat_assistant.py
```

### Scheduled daily email digest

A GitHub Actions workflow that emails you new job matches once a day without you opening the
app. This is a separate, more involved setup (a Gmail app password, a private repo for your
resume, and several GitHub secrets) — see
[`GITHUB_ACTIONS_SETUP.md`](./GITHUB_ACTIONS_SETUP.md) for the full walkthrough.

## Troubleshooting

- **`KeyError: 'GEMINI_API_KEY'`** (or similar for the other keys) — your `.env` file is
  missing, in the wrong location (it must be in the repo root, not inside `app/`), or missing
  that specific variable. Double check it against `.env.example`.
- **Rate-limited by Gemini** — the app automatically retries with backoff and shows a warning
  with how long it waited. If this happens often, try searching fewer companies or fewer
  resumes at once.
- **"File too large" on resume upload** — uploads are capped at 1 MB
  (`app/.streamlit/config.toml`) as a safeguard; resumes are normally a few hundred KB at most,
  so this usually means the wrong file was selected.
- **A search returns nothing for a company you just added** — brand-new companies aren't in
  the shared job cache yet; the app automatically falls back to a live fetch for anything not
  cached, so this should resolve itself on that first search.

## Project structure

```
jobfinder-ai/
├── .env.example                # Template for your API keys — copy to .env
├── requirements.txt             # Core dependencies for the Streamlit app
├── GITHUB_ACTIONS_SETUP.md      # Optional: scheduled daily email digest setup
├── app/
│   ├── streamlit_app.py         # Main entry point — the Streamlit UI
│   ├── api_server.py            # Optional: FastAPI backend for the React frontend
│   ├── chat_assistant.py        # Optional: terminal-based conversational interface
│   ├── requirements.txt         # Extra deps needed only for api_server.py
│   ├── database.py              # SQLite schema + auto-init (creates app/jobfinder.db)
│   ├── matcher.py                # Resume-to-job scoring logic (Gemini calls)
│   ├── job_aggregator.py         # Combines Workday + JSearch + Adzuna results
│   ├── connectors/                # Workday, JSearch, and Adzuna API clients
│   ├── resume_parser.py          # Extracts text from uploaded .pdf/.docx resumes
│   ├── resume_builder.py         # Generates ATS-optimized resume exports
│   ├── resume_tailor.py          # Tailors a resume to a specific job posting
│   ├── pdf_export.py             # Match-report PDF export
│   ├── email_sender.py           # Used by the optional scheduled email digest
│   └── run_scheduled_search.py   # Entry point for the GitHub Actions daily digest
└── frontend/                    # Optional: React UI for api_server.py (see above)
    ├── package.json
    └── src/
        ├── App.jsx
        ├── api.js
        └── components/          # ResumeLibrary, SearchPanel, JobCard, CompanyManager
```
