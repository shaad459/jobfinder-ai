# Setting up the daily email alert

One-time setup for `.github/workflows/daily-job-alert.yml`, which runs `run_scheduled_search.py`
once a day on GitHub's servers and emails you any new Strong/Good matches.

## 1. Generate a Gmail App Password

You do **not** need a new Google account - App Passwords work with your existing one, but only
once 2-Step Verification is turned on.

1. If you haven't already, turn on 2-Step Verification: `myaccount.google.com/security` → "2-Step
   Verification" → follow the prompts.
2. Go to `myaccount.google.com/apppasswords`.
3. Create a new app password (name it something like "JobScout AI").
4. Copy the 16-character password it gives you - you'll paste it into a GitHub secret below.
   This is **not** your normal Gmail password and can be revoked independently at any time from
   the same page.

## 2. Base64-encode your resume

The workflow needs your resume as a GitHub secret (secrets are encrypted and GitHub auto-redacts
exact matches from log output, so this never appears in plaintext anywhere in the repo or its
history). One catch: a single GitHub secret is capped at 48 KB, and this resume's base64 text is
around 106 KB - too big for one secret, so it needs to be split across three
(`RESUME_BASE64_1`, `RESUME_BASE64_2`, `RESUME_BASE64_3`) and reassembled by the workflow at
runtime (already wired up in `daily-job-alert.yml`).

**Windows (PowerShell) - run `encode_resume.ps1`:**
A ready-made script (delivered alongside this doc) does the splitting for you - it writes
`chunk_1.txt`, `chunk_2.txt`, `chunk_3.txt` to a temp folder and tells you exactly how many
chunks your resume produced (usually 3; could be fewer if you swap in a smaller resume, or more
if you use a larger one). Run it, then open each `chunk_N.txt` in Notepad, select-all/copy its
full contents, and paste into the matching `RESUME_BASE64_N` secret in step 3.

**Mac/Linux:**
```bash
split -b 45000 -d <(base64 -i "app/sample_data/Shaad Khan Product Owner.pdf") /tmp/resume_chunk_
# writes /tmp/resume_chunk_00, _01, _02, ... - paste each into RESUME_BASE64_1, _2, _3 in order
```

If your resume is a `.docx` instead of `.pdf`, also change `RESUME_EXT: pdf` to
`RESUME_EXT: docx` near the top of `.github/workflows/daily-job-alert.yml` before you push it -
`extract_resume_text()` picks its parser from the file extension, so this has to match.

## 3. Add repository secrets

On GitHub: your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository
secret**. Add each of these (values from your local `.env` for the API keys):

| Secret name | Value |
|---|---|
| `RESUME_BASE64_1` | contents of `chunk_1.txt` from step 2 |
| `RESUME_BASE64_2` | contents of `chunk_2.txt` from step 2 |
| `RESUME_BASE64_3` | contents of `chunk_3.txt` from step 2 (if your script produced more or fewer chunks, add/remove secrets - and matching lines in the workflow's "Write resume from secret" step - to match) |
| `GEMINI_API_KEY` | same as your local `.env` |
| `JSEARCH_API_KEY` | same as your local `.env` |
| `ADZUNA_APP_ID` | same as your local `.env` |
| `ADZUNA_APP_KEY` | same as your local `.env` |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 1 |
| `NOTIFY_EMAIL` | *(optional)* only if you want the digest sent somewhere other than `GMAIL_ADDRESS` itself |

None of these ever appear in your code or commit history - they're injected as environment
variables only at the moment the workflow runs, and GitHub scrubs any exact-match occurrence out
of the logs automatically.

## 4. Push the new files

`email_sender.py`, `run_scheduled_search.py`, and `.github/workflows/daily-job-alert.yml` are all
safe to commit - none of them contain a real key or your resume, only code and placeholders read
from the environment at runtime.

## 5. Test it

Go to your repo's **Actions** tab → "Daily job alert" → **Run workflow** (the manual trigger,
`workflow_dispatch`) rather than waiting for the schedule. Watch the run's logs for errors, and
check your inbox once it finishes. The very first run will likely email you a fairly large batch
(everything currently open across your 5 configured companies) since nothing's been scored yet -
after that, each day's email should only contain postings that are genuinely new.

## One thing worth knowing

This workflow keeps its own `jobfinder.db`, persisted in GitHub's Actions cache - it is a
**separate database from the one on your machine** that Streamlit reads. They don't share an
"already scored" list. Practically: a job the scheduled workflow already emailed you about might
still show up as "new" the next time you search manually from the Streamlit app on your own
computer, and vice versa. For a single-user, no-shared-hosting setup like this, that's the
tradeoff for not standing up a shared database (which is what the old Turso-based design in
`PHASE6_EMAIL_ALERTS_SPEC.md` was for, before the project moved to "runs entirely on your own
machine" - that doc is now superseded and can be ignored/deleted).
