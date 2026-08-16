import json
import re
import job_similarity
from gemini_utils import call_gemini
from connectors.workday_connector import fetch_workday_job_description
from repository import get_matches

MATCH_PROMPT_TEMPLATE = """You are a job-matching assistant. Given a candidate's profile and a batch of job
listings, score how well each job fits the candidate.

Candidate profile:
{profile_json}

Jobs (indexed):
{jobs_json}

GROUNDING RULE (critical): Only credit the candidate with a skill, domain, or specialization if it is
explicitly present in their profile (skills list, job titles, or summary) - or a clear, unambiguous
synonym. Do NOT assume a broader category covers a narrower specialization. For example, "financial
services" experience does NOT automatically mean the candidate has "payments" or "pricing" experience
specifically - those must appear explicitly to be credited. When a job's title or description names a
specific required domain, technology, or certification, check literally whether it (or a clear synonym)
appears in the candidate's profile.

For EACH job, return:
- "tier": "Strong" ONLY if ALL of the job's explicitly stated hard requirements (must-haves, required
  skills, required domain experience) are genuinely present in the candidate's profile. If even one
  clearly-required, specific item is missing, the tier must be "Good" (if the rest of the fit is strong)
  or "Weak" (if the mismatch is significant) - never "Strong". See also the certifications/preferred-
  skills rule below, which can also cap a tier at "Good" even when all required items are met.
- "score": 0-100, consistent with the tier.
- "matching_points": concrete, specific things from the candidate's profile that genuinely match this
  job - cite the actual matching skill/keyword, not a vague category.
- "gaps": concrete things the job appears to require that are NOT present in the candidate's profile.
  Be specific, and flag anything that reads as non-negotiable. Empty list if there are truly no gaps.
- "reasoning": one short sentence summarizing the overall verdict.
- "dimension_breakdown": a structured, auditable breakdown across exactly six dimensions - "role",
  "location", "skills", "certification", "experience", "domain" - so a human can see precisely which
  aspects of the match are strong and which aren't, rather than just a single opaque score. For EACH of
  the six dimensions, return an object with:
  - "level": "match" if this dimension is a clear, strong fit; "partial" if there's real but incomplete
    overlap (e.g. the candidate has some but not all of the skills/certifications the job wants, or is
    close on years of experience without fully meeting a stated minimum); "none" if this dimension is a
    clear mismatch or the job states no requirement here at all.
  - "note": one short, specific phrase grounding the verdict, e.g. "Candidate has 10 years vs. required
    8+" or "Job wants AWS certification; not present in candidate profile."
  Judge each dimension the same way you judge the rest of this assessment - using real understanding of
  synonyms and equivalent wording (e.g. a "Product Owner" background counts toward a "Product Manager"
  role dimension; "Pune" and "Pune, Maharashtra" are the same location) - never literal string matching.

CERTIFICATIONS AND "PREFERRED" SKILLS (in addition to the hard-requirements rule above): jobs often name
a specific certification, skill, or domain as "preferred" rather than required - e.g. "CSM preferred",
"PSPO preferred", "knowledge of {{domain}} strongly preferred". Check literally whether each explicitly
named preferred item appears in the candidate's profile:
- If the candidate has EVERY explicitly named preferred item (in addition to meeting all required
  items), this is a genuine positive signal supporting a "Strong" tier - credit it by name in
  "matching_points".
- If the job explicitly names one or more preferred items and the candidate's profile does NOT show
  them, the match should NOT be rated "Strong" even if every required item is met - cap it at "Good"
  instead, and list each missing preferred item in "gaps", prefixed with "(preferred, not required): "
  so it's clear it's not a blocking mismatch, just a missed nice-to-have.
- A job that names no preferred items at all is unaffected by this rule.

If a job has no description available (only title/company/location), rely only on the title and company,
be more conservative about awarding "Strong", and note in "gaps" that full requirements are unknown due
to missing description.

Return ONLY a valid JSON array (no markdown fences, no extra text), in this exact shape:
[
  {{
    "index": 0,
    "tier": "Strong",
    "score": 92,
    "matching_points": ["10 years in Product Management", "Agile/Scrum backlog ownership"],
    "gaps": [],
    "reasoning": "short summary",
    "dimension_breakdown": {{
      "role": {{"level": "match", "note": "Product Owner background matches Product Manager role"}},
      "location": {{"level": "match", "note": "Both Pune"}},
      "skills": {{"level": "match", "note": "SQL, Python, Gen AI all present"}},
      "certification": {{"level": "match", "note": "PSPO and CSM both held"}},
      "experience": {{"level": "match", "note": "10 years vs. 10 years required"}},
      "domain": {{"level": "match", "note": "Both banking"}}
    }}
  }}
]
"""


PRESCREEN_PROMPT_TEMPLATE = """You are doing a fast, coarse first-pass screen before detailed scoring. Given a
candidate's profile and a batch of job listings, decide only whether each job is AT LEAST PLAUSIBLY worth a
detailed match assessment - this is not a final verdict.

Candidate profile:
{profile_json}

Jobs (indexed):
{jobs_json}

Err on the side of inclusion: mark a job NOT plausible only if it is CLEARLY and OBVIOUSLY a mismatch - a
different domain entirely, a wildly different seniority level, or a hard requirement (a specific license, a
specific technical stack) that the candidate's profile clearly and entirely lacks. If there is any reasonable
chance the job could turn out Strong or Good on a detailed pass, mark it plausible - the detailed pass will
catch anything this screen is too generous about, but nothing catches what this screen wrongly excludes.

Return ONLY a valid JSON array (no markdown fences, no extra text), in this exact shape:
[
  {{"index": 0, "plausible": true}},
  {{"index": 1, "plausible": false}}
]
"""

# Jobs that don't pass the prescreen are tagged with this rather than silently dropped, so nothing
# disappears from the results - they're just marked as not having gone through detailed scoring.
SCREENED_OUT_PLACEHOLDER = {
    "match_tier": "Weak",
    "match_score": 0,
    "match_points": [],
    "match_gaps": ["Not evaluated in detail - screened out before detailed scoring as an unlikely match."],
    "match_reasoning": "Filtered by the prescreen pass; treat as low-confidence, not a definitive verdict.",
    "dimension_breakdown": {},
}

# --- Stage 0: cheap, non-Gemini pre-filter -----------------------------------------------
#
# Runs before either Gemini stage. Two independent checks, both deliberately conservative
# (fail open - keep the job - whenever the signal is ambiguous or absent), meant only to
# catch obviously-irrelevant jobs cheaply before they ever cost a Gemini call:
#
#   1. Title/keyword relevance against the candidate's profile job titles.
#   2. An explicit experience-level mismatch (entry-level vs. senior candidate, or a stated
#      required-years figure well above the candidate's actual experience).
#
# Applies uniformly to every job regardless of source (Workday, JSearch, Adzuna) - the check
# itself only looks at title/description text and the candidate's profile, neither of which
# differ by source.

_TITLE_STOPWORDS = {
    "a", "an", "the", "of", "in", "at", "for", "and", "or", "to", "with", "on", "is",
    "senior", "sr", "junior", "jr", "lead", "principal", "staff", "associate", "entry",
    "intern", "new", "i", "ii", "iii", "iv",
}

_ENTRY_LEVEL_TITLE_MARKERS = (
    "entry level", "entry-level", "junior", "intern", "internship", "trainee",
    "fresher", "graduate program", "grad program", "new grad", "apprenticeship",
    "early career", "early in career",
)

_EXPERIENCE_YEARS_PATTERN = re.compile(
    r"(\d{1,2})\+?\s*(?:-|–|to)?\s*\d{0,2}\+?\s*years?\s*(?:of\s+)?(?:relevant\s+|professional\s+)?experience",
    re.IGNORECASE,
)

OVER_EXPERIENCE_BUFFER = 2   # skip if a role requires candidate_years + this many, or more
JUNIOR_YEARS_CAP = 2         # an explicit "0-2 years" style requirement also counts as entry-level


def _title_keywords(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _TITLE_STOPWORDS and len(w) > 1}


def _title_matches_reference(job_title: str, reference_titles: list[str]) -> bool:
    """Requires the job title to share at least one meaningful keyword with at least one of the
    given reference titles - e.g. a "Product Owner" reference and a "Product Manager" posting
    share "product," while a "Software Engineer" posting shares nothing and is correctly
    excluded. Deliberately lenient (ANY overlap, not ALL): title wording varies a lot between a
    reference title and a given employer's own titles, and this check is only meant to catch
    obviously unrelated roles cheaply - real relevance judgment is still the prescreen stage's
    job.

    Fails open (returns True) if there are no reference titles, or the job has no usable title
    text to compare against.
    """
    reference_keywords = set()
    for t in (reference_titles or []):
        reference_keywords |= _title_keywords(t)

    if not reference_keywords:
        return True

    job_keywords = _title_keywords(job_title)
    if not job_keywords:
        return True

    return bool(reference_keywords & job_keywords)


def _title_matches_profile(job_title: str, profile: dict) -> bool:
    """Same check as _title_matches_reference, using the candidate's own profile.job_titles as
    the reference - the default behavior when no explicit search-title override is in play.
    """
    return _title_matches_reference(job_title, profile.get("job_titles") or [])


def _extract_required_years(text: str) -> list[int]:
    return [int(m.group(1)) for m in _EXPERIENCE_YEARS_PATTERN.finditer(text or "")]


def _passes_experience_filter(job: dict, candidate_years) -> tuple[bool, str | None]:
    """Excludes a job only on a clear, explicit experience-level mismatch - never on the
    absence of a signal. Returns (passes, reason_if_excluded).

    1. Entry-level: the title reads as junior/entry-level/intern/etc. while the candidate has
       meaningfully more experience than JUNIOR_YEARS_CAP.
    2. Over-experienced-requirement: the SMALLEST explicit "X years of experience" figure found
       anywhere in the title+description is still >= candidate_years + OVER_EXPERIENCE_BUFFER.
       Using the smallest (most charitable) figure found - rather than the largest - means a
       posting that mentions one high number in passing (e.g. "10+ years of company history")
       alongside a lower, more relevant figure doesn't get wrongly excluded.

    If candidate_years is unknown (None), this filter is a no-op (fails open).
    """
    if candidate_years is None:
        return True, None

    title = job.get("title") or ""
    description = job.get("description") or ""
    combined_text = f"{title}\n{description}"
    title_lower = title.lower()

    if any(marker in title_lower for marker in _ENTRY_LEVEL_TITLE_MARKERS) and candidate_years > JUNIOR_YEARS_CAP:
        return False, (f"Title reads as entry-level/junior; candidate profile shows "
                        f"{candidate_years} years of experience.")

    required_years = _extract_required_years(combined_text)
    if required_years:
        min_required = min(required_years)

        if min_required <= JUNIOR_YEARS_CAP and (candidate_years - min_required) >= 3:
            return False, (f"Posting explicitly caps required experience at {min_required} "
                            f"year(s); candidate has {candidate_years} years - likely overqualified.")

        if min_required >= candidate_years + OVER_EXPERIENCE_BUFFER:
            return False, (f"Posting requires at least {min_required} years of experience; "
                            f"candidate profile shows {candidate_years} years.")

    return True, None


def _prefiltered_placeholder(reason: str) -> dict:
    return {
        "match_tier": "Weak",
        "match_score": 0,
        "match_points": [],
        "match_gaps": [reason],
        "match_reasoning": ("Excluded before Gemini by the keyword/experience pre-filter - "
                             "not a Gemini-verified verdict."),
        "dimension_breakdown": {},
    }


# --- Gemini stages -------------------------------------------------------------------------


def prescreen_jobs_batch(profile: dict, jobs_batch: list[dict]) -> list[dict]:
    jobs_for_prompt = [
        {
            "index": i,
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "description": (job.get("description") or "")[:500],
        }
        for i, job in enumerate(jobs_batch)
    ]

    prompt = PRESCREEN_PROMPT_TEMPLATE.format(
        profile_json=json.dumps(profile, indent=2),
        jobs_json=json.dumps(jobs_for_prompt, indent=2),
    )

    response = call_gemini(prompt, model="gemini-3.5-flash-lite")
    raw_output = response.output_text.strip()
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        raw_output = raw_output.replace("json\n", "", 1)

    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        print("Could not parse prescreen JSON. Raw model output was:")
        print(raw_output)
        raise


def score_jobs_batch(profile: dict, jobs_batch: list[dict]) -> list[dict]:
    jobs_for_prompt = [
        {
            "index": i,
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "description": (job.get("description") or "")[:1500],
        }
        for i, job in enumerate(jobs_batch)
    ]

    prompt = MATCH_PROMPT_TEMPLATE.format(
        profile_json=json.dumps(profile, indent=2),
        jobs_json=json.dumps(jobs_for_prompt, indent=2),
    )

    response = call_gemini(prompt, model="gemini-3.5-flash")
    raw_output = response.output_text.strip()
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        raw_output = raw_output.replace("json\n", "", 1)

    try:
        return json.loads(raw_output)
    except json.JSONDecodeError:
        print("Could not parse JSON. Raw model output was:")
        print(raw_output)
        raise


def _enrich_missing_descriptions(jobs: list[dict]) -> list[dict]:
    enriched = []
    to_enrich = [j for j in jobs if j.get("source") == "workday" and not j.get("description")]
    if to_enrich:
        print(f"Fetching full descriptions for {len(to_enrich)} Workday jobs...")

    for i, job in enumerate(jobs):
        job = dict(job)
        if job.get("source") == "workday" and not job.get("description"):
            try:
                job["description"] = fetch_workday_job_description(job)
                print(f"  [{i + 1}/{len(jobs)}] fetched: {job.get('title')}")
            except Exception as e:
                print(f"Warning: could not fetch description for {job.get('title')} @ {job.get('company')}: {e}")
        enriched.append(job)
    return enriched


def score_all_jobs(profile: dict, jobs: list[dict], batch_size: int = 10,
                    prescreen_batch_size: int = 20, on_batch_scored=None,
                    title_override: str = None) -> list[dict]:
    """Four-stage pipeline, to conserve the precise model's much tighter daily quota.

    Stage 0 - cheap pre-filter (no Gemini): title-keyword relevance against a reference title
    (see title_override below), then (on the survivors, and only after description enrichment)
    an explicit experience-level mismatch check. Both fail open - a job is only excluded on a
    clear signal, never on the absence of one. Excluded jobs are saved with a placeholder
    verdict, same as prescreen rejects, so they're never re-checked on a future run. The
    title check runs BEFORE Workday description enrichment specifically so an obviously
    wrong-department Workday job never triggers its per-job description fetch at all.

    title_override, when given, replaces the candidate's own profile.job_titles as the
    reference for the Stage 0 title check - set by chat_assistant.py whenever the user
    explicitly searched for a specific role (e.g. "search product owner roles"), so the filter
    checks against what was actually searched for, not blindly against the resume. This matters
    even when the searched role IS on the resume: it means only jobs actually related to that
    title (e.g. "Product Owner", "Product Manager") reach Gemini, while unrelated ones (e.g.
    "Software Engineer", "Accountant") are still excluded for free - earlier this filtered
    everything through instead, which meant dozens of irrelevant postings burned real prescreen
    calls just to be rejected there instead of for free here. When title_override is None (a
    plain company search with no explicit role), behavior is unchanged - the candidate's own
    profile.job_titles is the reference, exactly as before.

    Stage 0c - history-based reuse/skip (no Gemini): compares each Stage-0 survivor against this
    same resume's own past Gemini-verified verdicts (see job_similarity.py) - a near-duplicate of
    an already-scored posting (e.g. re-posted, or surfaced again via a different job source)
    reuses that verdict outright, and a job similar enough to a past Weak match for a
    candidate-intrinsic reason (not location/role) is treated the same way. Only a genuine
    near-duplicate can inherit a positive verdict - never auto-assigns Strong/Good just from
    moderate similarity.

    Stage 1 - prescreen (gemini-3.5-flash-lite, cheap, large batches): a fast, deliberately
    generous plausibility check on whatever survives Stage 0c. Jobs that fail are tagged "Weak"
    and saved as-is rather than dropped, so nothing silently disappears.

    Stage 2 - precise scoring (gemini-3.5-flash, smaller batches): the grounded Strong/Good/
    Weak assessment (including certification/preferred-skill weighting), run only on jobs that
    passed the prescreen.

    If on_batch_scored is given, it's called with each batch's results (from any stage) as
    soon as they're ready, so progress survives even if a later batch fails - e.g. a rate limit
    that exhausts call_gemini's retries. A failed batch is skipped rather than aborting the run;
    since matches are deduped by (profile_id, job_url), a skipped batch is simply retried next
    run. A failed *prescreen* batch is treated as "everything plausible" rather than being
    skipped - if the screen itself breaks, jobs fail open to detailed scoring rather than being
    lost.
    """
    scored_jobs = []

    # --- Stage -1: is the SEARCHED role even plausible for this candidate at all? ---
    #
    # Stage 0a below checks whether each JOB's title matches what was searched for - it does
    # NOT check whether the searched title itself has anything to do with the candidate. If
    # someone searches "Software Engineer" while their whole resume is Product/BA work, every
    # real Software Engineer posting would still pass Stage 0a (its title matches the search
    # term) and go all the way to Gemini, which would - correctly, but expensively - call it a
    # mismatch. This stage catches that upfront, once per search rather than once per job: if
    # title_override shares no keyword with ANY title in the candidate's own work history,
    # nothing in this search can plausibly match, so every job is tagged and returned without a
    # single Gemini call. Only applies when title_override is set (an explicit role search) and
    # the profile actually has job_titles to check against - fails open otherwise, same
    # philosophy as every other filter in this file.
    candidate_titles = profile.get("job_titles") or []
    if title_override and candidate_titles and not _title_matches_reference(title_override, candidate_titles):
        reason = (
            f"Searched role \"{title_override}\" shares no keyword with anything in your "
            f"resume's work history ({', '.join(candidate_titles)}) - skipped before any Gemini "
            f"call, since it can't plausibly be a match."
        )
        print(f"Skipping Gemini entirely for this search: {reason}")
        scored_jobs = [dict(job, **_prefiltered_placeholder(reason)) for job in jobs]
        if on_batch_scored:
            on_batch_scored(scored_jobs)
        return scored_jobs

    # --- Stage 0a: title-keyword relevance (before description enrichment) ---
    reference_titles = [title_override] if title_override else (profile.get("job_titles") or [])
    reference_label = f'"{title_override}"' if title_override else "the candidate's profile job titles"

    title_relevant = []
    title_filtered = []
    for job in jobs:
        if _title_matches_reference(job.get("title") or "", reference_titles):
            title_relevant.append(job)
        else:
            job = dict(job)
            job.update(_prefiltered_placeholder(
                f"Title '{job.get('title')}' shares no keyword with {reference_label}."
            ))
            title_filtered.append(job)

    if title_filtered:
        print(f"{len(title_filtered)}/{len(jobs)} job(s) excluded before Gemini - title doesn't "
              f"match {reference_label}.")
        scored_jobs.extend(title_filtered)
        if on_batch_scored:
            on_batch_scored(title_filtered)

    jobs = _enrich_missing_descriptions(title_relevant)

    # --- Stage 0b: experience-level mismatch (needs description text, hence after enrichment) ---
    candidate_years = profile.get("total_years_experience")
    experience_ok = []
    experience_filtered = []
    for job in jobs:
        passes, reason = _passes_experience_filter(job, candidate_years)
        if passes:
            experience_ok.append(job)
        else:
            job = dict(job)
            job.update(_prefiltered_placeholder(reason))
            experience_filtered.append(job)

    if experience_filtered:
        print(f"{len(experience_filtered)}/{len(jobs)} job(s) excluded before Gemini - experience "
              f"level mismatch.")
        scored_jobs.extend(experience_filtered)
        if on_batch_scored:
            on_batch_scored(experience_filtered)

    jobs = experience_ok
    total_excluded_free = len(title_filtered) + len(experience_filtered)
    print(f"{len(jobs)} job(s) remain for Gemini prescreen ({total_excluded_free} excluded at zero cost).")

    # --- Stage 0c: history-based reuse/skip (no Gemini call - see job_similarity.py) ---
    #
    # Runs after title/experience filtering (so only already-plausible jobs are compared, which
    # is both cheaper and safer) and before the prescreen, against this SAME profile's own past
    # Gemini-verified verdicts (repository.get_matches). Two outcomes, both zero-cost:
    #   - "reuse": a near-duplicate of an already-scored posting at the same company (e.g. the
    #     same listing surfaced again via a different source, or reposted later) - its verdict is
    #     copied over outright, Gemini is never called for this job.
    #   - "skip_weak": similar enough to a posting that already scored Weak for a
    #     candidate-intrinsic reason (a missing skill/certification/domain/experience, not
    #     location or role) that the same gap almost certainly still applies.
    # Never auto-assigns Strong/Good from a merely-similar (not near-duplicate) job - only a
    # genuine near-duplicate can inherit a positive verdict; see job_similarity.py's own
    # docstring for the full reasoning. History is fetched once per call, not once per job.
    history = get_matches(profile["id"]) if profile.get("id") is not None else []
    history_resolved = []
    still_unscored = []
    if history:
        for job in jobs:
            action = job_similarity.find_history_action(job, history)
            if action is None:
                still_unscored.append(job)
            elif action["action"] == "reuse":
                history_resolved.append(job_similarity.apply_reuse(job, action))
            else:  # "skip_weak"
                history_resolved.append(job_similarity.apply_skip_weak(job, action))
    else:
        still_unscored = jobs

    if history_resolved:
        print(f"{len(history_resolved)}/{len(jobs)} job(s) resolved from this resume's own scoring "
              f"history - no Gemini call needed.")
        scored_jobs.extend(history_resolved)
        if on_batch_scored:
            on_batch_scored(history_resolved)

    jobs = still_unscored

    # --- Stage 1: prescreen ---
    plausible_jobs = []
    total_prescreen_batches = (len(jobs) + prescreen_batch_size - 1) // prescreen_batch_size

    for batch_num, start in enumerate(range(0, len(jobs), prescreen_batch_size), start=1):
        batch = jobs[start:start + prescreen_batch_size]
        print(f"Prescreening batch {batch_num}/{total_prescreen_batches} ({len(batch)} jobs)...")

        try:
            verdicts = prescreen_jobs_batch(profile, batch)
        except Exception as e:
            print(f"Warning: prescreen batch {batch_num}/{total_prescreen_batches} failed - "
                  f"treating all its jobs as plausible so nothing is lost to a screening error: {e}")
            plausible_jobs.extend(batch)
            continue

        verdict_by_index = {v.get("index"): v.get("plausible", True) for v in verdicts}
        screened_out_batch = []
        for i, job in enumerate(batch):
            if verdict_by_index.get(i, True):
                plausible_jobs.append(job)
            else:
                job = dict(job)
                job.update(SCREENED_OUT_PLACEHOLDER)
                screened_out_batch.append(job)

        scored_jobs.extend(screened_out_batch)
        if screened_out_batch and on_batch_scored:
            on_batch_scored(screened_out_batch)

    print(f"{len(plausible_jobs)}/{len(jobs)} jobs passed the prescreen and will get detailed scoring")

    # --- Stage 2: precise scoring, same logic as before, just on the smaller plausible set ---
    total_batches = (len(plausible_jobs) + batch_size - 1) // batch_size

    for batch_num, start in enumerate(range(0, len(plausible_jobs), batch_size), start=1):
        batch = plausible_jobs[start:start + batch_size]
        print(f"Scoring batch {batch_num}/{total_batches} ({len(batch)} jobs)...")

        try:
            assessments = score_jobs_batch(profile, batch)
        except Exception as e:
            print(f"Warning: batch {batch_num}/{total_batches} failed and was skipped "
                  f"(it will be retried on the next run): {e}")
            continue

        batch_scored = []
        for assessment in assessments:
            idx = assessment.get("index")
            if idx is None or idx >= len(batch):
                continue
            job = dict(batch[idx])
            job["match_tier"] = assessment.get("tier")
            job["match_score"] = assessment.get("score")
            job["match_points"] = assessment.get("matching_points", [])
            job["match_gaps"] = assessment.get("gaps", [])
            job["match_reasoning"] = assessment.get("reasoning")
            job["dimension_breakdown"] = assessment.get("dimension_breakdown", {})
            batch_scored.append(job)

        scored_jobs.extend(batch_scored)
        if on_batch_scored:
            on_batch_scored(batch_scored)

    return scored_jobs
