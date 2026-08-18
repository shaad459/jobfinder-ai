"""Conversational entry point for JobScout AI (BUILD_PLAN.md item 6) - a chat loop that wraps the
same search/match/export/tailor pipeline test_matcher.py drives step-by-step, but through free-text
commands instead of a fixed sequence of prompts. test_matcher.py is left in place as a quick,
non-interactive way to batch-test the pipeline; this is the interactive way to actually use it.

Session state kept in memory for the life of the process:
- profile / profile_id: the candidate's normalized profile, extracted once (or loaded from cache)
  at startup, same as test_matcher.py.
- last_matches: the results of the most recent search, so "open the SQL one" or "export this to
  PDF" can refer back to what was just shown without re-fetching or re-scoring anything.
- pending_clarification: set when the assistant just asked "which one did you mean?" - the NEXT
  message is treated as the answer to that question, not a new command.

Framework note: BUILD_PLAN.md flagged LangGraph as a genuine fit for the "which one did you mean?"
disambiguation (its interrupt/resume pattern matches this exactly) as an alternative to hand-rolling
pending_clarification. This first version hand-rolls it instead - one dict, one flag - since the
disambiguation flow here is small and fixed; LangGraph is a reasonable upgrade later if you want
that specific pattern for its own sake, not because the hand-rolled version is broken.

Intent parsing uses gemini-3.5-flash-lite (cheap, same model as the prescreen stage) - one call per
message you type, never the tight-quota precise-scoring model. "send_email" is recognized as an
intent (per the original plan's action list) but just prints a not-yet-available message, since
email_sender.py (BUILD_PLAN.md item 9) hasn't been built yet - it's blocked on you generating a
Gmail App Password.

"tailor_resume" is a new intent beyond BUILD_PLAN.md's original four (search/open_job/export_pdf/
send_email) - added because resume_tailor.py already exists and fits naturally as a chat action,
arguably more naturally than the numbered-list flow test_matcher.py currently offers it through.
"""

import json
import webbrowser
from pathlib import Path
from gemini_utils import call_gemini
from resume_parser import extract_resume_text
from profile_extractor import extract_structured_profile
from resume_builder import build_ats_resume_pdf
from resume_tailor import tailor_profile_for_job, tailored_resume_filename
from job_aggregator import fetch_company_jobs
from matcher import score_all_jobs
from pdf_export import export_matches_to_pdf
from database import init_db
from repository import (
    get_or_create_profile, get_profile_by_hash, save_job, save_match,
    get_scored_job_urls, get_matches, delete_stale_jobs, mark_job_opened, get_all_company_names,
)

INTENT_PROMPT_TEMPLATE = """You are the command interpreter for a job-search assistant. Classify the user's
message into exactly one intent and extract any relevant fields. Return ONLY valid JSON (no markdown fences,
no extra text) in this exact shape:

{{
  "intent": "search" | "open_job" | "tailor_resume" | "export_pdf" | "send_email" | "chat",
  "company": "a company name mentioned, or null",
  "all_companies": true or false or null (true if the user wants to search across every configured company
    rather than one specific one, e.g. "search all companies", "check everywhere", "try every one" -
    null/false otherwise),
  "title": "a specific job title/role the user explicitly wants to search for, e.g. 'product manager',
    'business analyst' - null if not mentioned. This can be a role that is NOT on the candidate's own
    resume - extract it anyway if the user explicitly asked to search for it, don't refuse or leave it
    null just because it doesn't match their background. Correct obvious spelling typos to the standard
    spelling of the role/word (e.g. 'onwer' -> 'owner', 'manger' -> 'manager', 'enginir' -> 'engineer') -
    output the corrected spelling, not the user's typo, since this text is used as a literal search
    query and a misspelled word won't match any real job posting.",
  "location": "a location mentioned, or null",
  "relocation_ok": true or false or null (true if the user says they're open to relocating or any location;
    false if they explicitly want to stay local; null if not mentioned),
  "keyword": "a specific title/skill fragment the user uses to point at ONE particular job among several they
    were already shown - e.g. 'the SQL one', 'the product manager role', 'citi' - null if not mentioned"
}}

Intent guide:
- "search": the user wants to search/find jobs at a company (or across all companies), e.g. "find jobs at
  Citi", "search Google near Pune", "look for product manager roles at Mastercard", "search all companies
  for product manager roles".
- "open_job": the user wants to open a specific job they were already shown, e.g. "open the Citi one", "show
  me the SQL role".
- "tailor_resume": the user wants a resume tailored/customized for one specific job they were already shown,
  e.g. "tailor my resume for the citi product owner job", "customize my resume for that one".
- "export_pdf": the user wants their current matches exported/saved as a PDF report.
- "send_email": the user wants their matches emailed to them.
- "chat": anything else - greetings, questions, unclear requests. Default here if genuinely unsure.

A message can set several fields at once - extract every field that applies, don't stop at the first one.
Examples:
- "search all companies for product owner roles" -> {{"intent": "search", "company": null,
  "all_companies": true, "title": "product owner", "location": null, "relocation_ok": null, "keyword": null}}
- "search for product owner roles, I'm ok with relocation" -> {{"intent": "search", "company": null,
  "all_companies": false, "title": "product owner", "location": null, "relocation_ok": true, "keyword": null}}
- "find jobs at Citi" -> {{"intent": "search", "company": "Citi", "all_companies": false, "title": null,
  "location": null, "relocation_ok": null, "keyword": null}}
- "search Google near Pune" -> {{"intent": "search", "company": "Google", "all_companies": false,
  "title": null, "location": "Pune", "relocation_ok": null, "keyword": null}}
- "search all companies for product onwer role" -> {{"intent": "search", "company": null,
  "all_companies": true, "title": "product owner", "location": null, "relocation_ok": null, "keyword": null}}

User message: "{message}"
"""

# Deterministic, zero-Gemini-call fallback for "search all companies" - this is a mechanical phrase
# match, not something that needs semantic judgment, so it's checked directly against the raw message
# rather than relying solely on the intent parser getting the "all_companies" boolean right. Also
# reused inside _do_search() itself, since a user can trigger the same intent by typing "all
# companies" in reply to the "which company?" fallback prompt, not just in their original message.
_ALL_COMPANIES_TRIGGERS = (
    "all companies", "every company", "each company", "all of them",
    "every one of them", "everywhere", "every configured company",
)


def _wants_all_companies(text: str) -> bool:
    text = (text or "").lower()
    return any(trigger in text for trigger in _ALL_COMPANIES_TRIGGERS)


def parse_intent(message: str) -> dict:
    prompt = INTENT_PROMPT_TEMPLATE.format(message=message.replace('"', "'"))
    response = call_gemini(prompt, model="gemini-3.5-flash-lite")
    raw_output = response.output_text.strip()
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        raw_output = raw_output.replace("json\n", "", 1)
    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        print("(couldn't parse that - treating it as general chat)")
        return {"intent": "chat", "company": None, "all_companies": None, "title": None,
                 "location": None, "relocation_ok": None, "keyword": None}


def _do_search(profile: dict, profile_id: int, intent: dict, include_aggregators: bool = False) -> list[dict]:
    company = intent.get("company")
    if not company:
        company = input("Which company would you like me to search? ").strip()
        if not company:
            print("No company given - cancelling search.")
            return []
        if _wants_all_companies(company):
            return _do_search_all_companies(profile, profile_id, intent)

    location = intent.get("location") or profile.get("current_location") or ""
    relocation_ok = bool(intent.get("relocation_ok"))

    # An explicit title override (e.g. "search product owner roles at Barclays") is both the
    # search query AND the Stage 0 title-relevance reference - jobs whose title shares no
    # keyword with THIS title are still excluded for free, same as normal, just checked against
    # what was actually asked for instead of blindly against the resume. That matters even when
    # the searched title is already on the resume (e.g. "product owner"): it keeps unrelated
    # postings (Software Engineer, Accountant, etc.) from burning a prescreen call just to be
    # rejected there instead of being filtered here for nothing.
    title_override = intent.get("title")
    if title_override:
        query = title_override
        print(f'Searching "{query}" specifically, as requested - only openings related to that '
              f"title will reach scoring, even if it's a different title than your resume's own.")
    else:
        query = (profile.get("job_titles") or ["product owner"])[0]

    jobs = fetch_company_jobs(company, query, location=location, relocation_ok=relocation_ok,
                               include_aggregators_for_workday=include_aggregators)
    print(f"Fetched {len(jobs)} jobs at {company}" +
          (" (any location)" if relocation_ok else f" (near {location})") +
          (" [Workday + JSearch/Adzuna]" if include_aggregators else ""))

    already_scored = get_scored_job_urls(profile_id)
    new_jobs = [j for j in jobs if j["url"] not in already_scored]
    print(f"{len(new_jobs)} are new (not previously scored)")

    def save_batch(batch_scored):
        for job in batch_scored:
            save_job(job)
            save_match(profile_id, job)

    if new_jobs:
        score_all_jobs(profile, new_jobs, batch_size=10, on_batch_scored=save_batch,
                        title_override=title_override)

    job_urls_this_search = {j["url"] for j in jobs}
    all_matches = get_matches(profile_id)
    matches_this_search = [m for m in all_matches if m["url"] in job_urls_this_search]

    print(f"\n--- Matches for {company} (freshest first) ---")
    if not matches_this_search:
        print("(no matches yet - jobs may still be scoring, or none passed prescreen)")
    for job in matches_this_search:
        posted = job.get("posted_date") or "date unknown"
        job_location = job.get("location") or "location not listed"
        opened = " (already opened)" if job.get("opened_at") else ""
        print(f"[{job['match_tier']:6}] {job['match_score']:3} - {job['title']} @ {job['company']} "
              f"({job_location}, {job['source']}, posted: {posted}){opened}")

        breakdown = job.get("dimension_breakdown") or {}
        if breakdown:
            for dimension in ("role", "location", "skills", "certification", "experience", "domain"):
                dim = breakdown.get(dimension) or {}
                level = dim.get("level")
                if not level:
                    continue
                note = dim.get("note")
                line = f"         {dimension}: {level}"
                if note:
                    line += f" - {note}"
                print(line)

    return matches_this_search


def _do_search_all_companies(profile: dict, profile_id: int, intent: dict) -> list[dict]:
    """"All companies" means the configured companies specifically (Workday + Greenhouse + Lever +
    Avature + Oracle Cloud Recruiting - see repository.get_all_company_names), not the broader
    "search anything, anywhere" approach
    BUILD_PLAN.md item 4 deliberately moved away from (it's what caused the real rate-limit
    stress earlier in the project) - looping the same company-scoped fetch_company_jobs() over a
    small, known company list keeps that same discipline.

    JSearch/Adzuna ARE included here (include_aggregators=True), on top of each company's own
    direct-connector feed - a normal single-company search ("search citi") stays connector-only
    and cheaper, but this sweep is already spending more calls by touching every configured
    company at once, so the extra 2 API calls per company for broader coverage is a reasonable
    trade here specifically.
    """
    companies = get_all_company_names()
    print(f"Searching all {len(companies)} configured companies "
          f"(Workday/Greenhouse/Lever/Avature/Oracle Cloud + "
          f"JSearch/Adzuna): {', '.join(companies)}")
    all_matches = []
    for company in companies:
        company_intent = dict(intent)
        company_intent["company"] = company
        all_matches.extend(_do_search(profile, profile_id, company_intent, include_aggregators=True))
    return all_matches


def _find_candidates(last_matches: list[dict], intent: dict, tiers: tuple) -> list[dict]:
    candidates = [j for j in last_matches if j.get("match_tier") in tiers]

    company = intent.get("company")
    if company:
        candidates = [j for j in candidates if company.lower() in (j.get("company") or "").lower()]

    keyword = intent.get("keyword")
    if keyword:
        kw = keyword.lower()
        candidates = [
            j for j in candidates
            if kw in (j.get("title") or "").lower()
            or any(kw in point.lower() for point in (j.get("match_points") or []))
        ]

    return candidates


def _print_candidate_list(candidates: list[dict]):
    print("Which one did you mean?")
    for i, job in enumerate(candidates, start=1):
        opened = " (already opened)" if job.get("opened_at") else ""
        print(f"  {i}. {job['title']} @ {job['company']} ({job['match_tier']}, "
              f"score {job['match_score']}){opened}")


def _resolve_clarification(message: str, candidates: list[dict]):
    """Returns the chosen job dict, the string "cancel", or None if the answer didn't resolve to
    exactly one candidate. Deliberately no Gemini call here - a number or a title-substring match
    covers the realistic cases cheaply.
    """
    text = message.strip().lower()
    if text in ("never mind", "nevermind", "cancel", "skip", "forget it"):
        return "cancel"
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
        return None
    matched = [j for j in candidates if text in (j.get("title") or "").lower()]
    if len(matched) == 1:
        return matched[0]
    return None


def _open_job(job: dict, profile_id: int):
    url = job.get("url")
    if not url:
        print("That job doesn't have a URL on file - can't open it.")
        return
    print(f"Opening: {job['title']} @ {job['company']}")
    # Only works because this runs locally on your own machine right now. Once this becomes a
    # hosted web app, "open this job" needs to be a clickable link in the UI instead - a server
    # can't launch a browser on your screen.
    webbrowser.open(url, new=1)
    mark_job_opened(profile_id, url)


def _tailor_resume(job: dict, profile: dict, resume_path: str):
    print(f"Tailoring resume for {job['title']} @ {job['company']}...")
    tailored_profile = tailor_profile_for_job(profile, job)
    tailored_path = tailored_resume_filename(resume_path, job["company"])
    build_ats_resume_pdf(tailored_profile, tailored_path)
    print(f"Tailored ATS resume saved to {tailored_path}")


def _export_pdf(last_matches: list[dict]):
    if not last_matches:
        print("No matches to export yet - search for a company first.")
        return
    output_path = export_matches_to_pdf(last_matches, "match_report.pdf")
    if output_path:
        print(f"Exported Strong/Good matches to {output_path}")
    else:
        print("No Strong or Good matches to export yet.")


init_db()

deleted = delete_stale_jobs(max_age_days=7)
if deleted:
    print(f"Cleaned up {deleted} job(s) older than 7 days (and their matches).")

resume_path = "sample_data/Shaad Khan Product Owner.pdf"
resume_text = extract_resume_text(resume_path)

cached = get_profile_by_hash(resume_text)
if cached:
    profile_id = cached.pop("id")
    profile = cached
    print(f"Using cached profile id {profile_id} (skipped Gemini extraction)")
else:
    profile = extract_structured_profile(resume_text)
    profile_id = get_or_create_profile(resume_text, profile)
    print(f"Using profile id {profile_id} (extracted fresh)")

ats_resume_path = str(Path(resume_path).stem) + " ATS.pdf"
build_ats_resume_pdf(profile, ats_resume_path)
print(f"ATS-friendly resume ready: {ats_resume_path}")

print("\nHi! I'm your job search assistant. Try things like:")
print('  "find jobs at Citi"  /  "search Google near Pune, I can relocate"')
print('  "open the SQL one"  /  "show me the product owner role"')
print('  "tailor my resume for the citi product owner job"')
print('  "export this to PDF"')
print("Type 'quit' or 'exit' to stop.\n")

last_matches = []
pending_clarification = None  # {"action": "open" | "tailor", "candidates": [...]}

while True:
    message = input("> ").strip()
    if not message:
        continue
    if message.lower() in ("quit", "exit"):
        print("Bye - good luck out there.")
        break

    if pending_clarification:
        resolved = _resolve_clarification(message, pending_clarification["candidates"])
        action = pending_clarification["action"]
        pending_clarification = None
        if resolved == "cancel":
            print("OK, never mind.")
        elif resolved is None:
            print("Didn't catch which one - try again, or say 'never mind'.")
        elif action == "open":
            _open_job(resolved, profile_id)
        elif action == "tailor":
            _tailor_resume(resolved, profile, resume_path)
        continue

    intent = parse_intent(message)
    kind = intent.get("intent")

    if kind == "search":
        if intent.get("all_companies") or _wants_all_companies(message):
            last_matches = _do_search_all_companies(profile, profile_id, intent)
        else:
            last_matches = _do_search(profile, profile_id, intent)

    elif kind == "open_job":
        candidates = _find_candidates(last_matches, intent, tiers=("Strong", "Good"))
        if not candidates:
            print("I don't see a match like that in your last search - try searching first, or rephrase.")
        elif len(candidates) == 1:
            _open_job(candidates[0], profile_id)
        else:
            pending_clarification = {"action": "open", "candidates": candidates}
            _print_candidate_list(candidates)

    elif kind == "tailor_resume":
        candidates = _find_candidates(last_matches, intent, tiers=("Strong",))
        if not candidates:
            print("No Strong match like that in your last search - tailoring is only offered for Strong "
                  "matches, since that's when it's worth spending the extra Gemini call.")
        elif len(candidates) == 1:
            _tailor_resume(candidates[0], profile, resume_path)
        else:
            pending_clarification = {"action": "tailor", "candidates": candidates}
            _print_candidate_list(candidates)

    elif kind == "export_pdf":
        _export_pdf(last_matches)

    elif kind == "send_email":
        print("Email sending isn't built yet - it needs a Gmail App Password set up first. Coming soon.")

    else:
        print('Not sure what you\'re asking - try "search <company>", "open <job>", '
              '"tailor my resume for <job>", or "export to pdf".')
