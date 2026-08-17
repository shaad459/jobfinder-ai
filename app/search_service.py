"""Search orchestration, shared by streamlit_app.py and api_server.py so the two frontends never
duplicate (and drift out of sync on) the actual search/scoring logic - only how results get
displayed differs between them.

Two layers here:
  - run_search_for_profile / run_search_for_profile_all_companies / run_search_captured: the
    original single-profile search flow (moved out of streamlit_app.py unchanged), which the
    Streamlit app still uses directly for its single "currently uploaded resume" flow.
  - run_search_for_profiles: NEW - fans the same single-profile flow out across several saved
    resumes at once and merges the results by job URL, so one job can carry multiple scores (e.g.
    "83% as Business Analyst, 71% as Product Manager") instead of assuming one score per job. This
    is what api_server.py's /api/search endpoint uses for the resume-library feature - see
    ARCHITECTURE.md / the resume-library design notes for why matching stays per-profile
    (independent dedup via get_scored_job_urls, independent Stage 0/-1 title filtering) rather
    than trying to score once and reuse the result across resumes.
"""
import contextlib
import io

from job_aggregator import fetch_company_jobs, get_company_jobs_from_cache
from matcher import score_all_jobs
from repository import (
    get_all_companies, get_matches, get_profile_by_id, get_scored_job_urls, save_job, save_match,
)

_JOB_DISPLAY_FIELDS = ("url", "title", "company", "location", "description", "posted_date", "source")


def run_search_for_profile(profile, profile_id, company, title_override, location, relocation_ok,
                            include_aggregators=False, skip_cache=False):
    query = title_override or (profile.get("job_titles") or ["product owner"])[0]

    # Cache-first, ALWAYS - not just when "search live" (skip_cache) is unchecked. This used to
    # skip reading the cache entirely whenever skip_cache was True, which meant anything the 12h
    # shared refresh (job_cache_sync.py) had already found for this company was silently dropped
    # from THIS run's job set just because you also wanted a live check - you'd have had to run
    # a second, cache-only search to see it again. Now the cache is always read, and a live fetch
    # is layered ON TOP of it - either because skip_cache is checked, or as the existing fallback
    # when the cache came up empty for this company - and the two are MERGED by url (never one
    # replacing the other), with the live copy winning on conflict since it's the freshest. Note
    # the cache is only as broad as refresh_job_cache.py's own query terms (see
    # search_queries_sync.py) - if you're searching a role family it doesn't cover, "search live"
    # is still what fills that gap, it just no longer discards the cache while doing it.
    cached_jobs = get_company_jobs_from_cache(company, location=location, relocation_ok=relocation_ok)
    live_jobs = []
    if skip_cache or not cached_jobs:
        live_jobs = fetch_company_jobs(company, query, location=location, relocation_ok=relocation_ok,
                                        include_aggregators_for_workday=include_aggregators)
        for job in live_jobs:
            save_job(job)

    jobs_by_url = {j["url"]: j for j in cached_jobs}
    jobs_by_url.update({j["url"]: j for j in live_jobs})
    jobs = list(jobs_by_url.values())

    already_scored = get_scored_job_urls(profile_id)
    new_jobs = [j for j in jobs if j["url"] not in already_scored]

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

    # Anything from this run's job set that STILL has no saved match at this point is genuinely
    # pending, not excluded - a prefilter/prescreen reject already gets a real placeholder
    # verdict saved via save_match (see matcher.py's _prefiltered_placeholder /
    # SCREENED_OUT_PLACEHOLDER), so it shows up in matches_this_search above like any other
    # scored job. Only a Stage 2 batch that itself failed (rate limit exhausted, etc.) leaves a
    # job with no match row at all - matcher.py deliberately leaves those unsaved so they're
    # retried on a future run rather than lost. Surfaced here as a "Pending" card instead of
    # silently vanishing, so a rate-limit storm mid-search doesn't just look like nothing found.
    scored_urls_this_search = {m["url"] for m in matches_this_search}
    pending = [
        {
            **job,
            "match_tier": "Pending",
            "match_score": None,
            "match_points": [],
            "match_gaps": [],
            "match_reasoning": "Not yet scored - will be picked up automatically on a future search.",
            "dimension_breakdown": {},
            "opened_at": None,
        }
        for job in jobs if job["url"] not in scored_urls_this_search
    ]

    return matches_this_search + pending


def run_search_for_profile_all_companies(profile, profile_id, title_override, location,
                                          relocation_ok, skip_cache=False):
    all_matches = []
    for company in get_all_companies():
        all_matches.extend(run_search_for_profile(
            profile, profile_id, company, title_override, location, relocation_ok,
            include_aggregators=True, skip_cache=skip_cache))
    return all_matches


def run_search_captured(*args, **kwargs):
    """Runs a search while capturing everything it prints, so the same diagnostics the CLI entry
    points show inline (prefilter/prescreen counts, freshness/location notes, warnings) are
    visible in a UI too, not just in the terminal running the server.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        all_companies = kwargs.pop("all_companies", False)
        if all_companies:
            result = run_search_for_profile_all_companies(*args, **kwargs)
        else:
            result = run_search_for_profile(*args, **kwargs)
    return result, buf.getvalue()


def run_search_for_profiles(profile_ids: list, companies: list = None, title_override: str = None,
                             location: str = "", relocation_ok: bool = False,
                             skip_cache: bool = False) -> dict:
    """Runs a search across EVERY given profile_id against the same set of companies, then merges
    results by job URL so each job carries a list of per-profile scores instead of one score per
    job - the core of the resume-library feature (see api_server.py's /api/search).

    companies=None means "every configured company" (get_all_companies()), same as the old
    "search all companies" checkbox, and also switches on include_aggregators the same way that
    path always did (broader JSearch/Adzuna coverage on top of each Workday feed).

    Each profile's search still runs its own independent Stage 0/-1 title filtering and its own
    get_scored_job_urls dedup (see matcher.py / repository.py) - a job irrelevant to your Business
    Analyst resume is excluded for free on that pass without affecting whether it reaches Gemini
    on your Product Manager resume's pass, and a job already scored for one resume is never
    re-sent to Gemini for THAT resume again, but is still scored fresh the first time a different
    resume searches it.

    Returns {"jobs": [...], "log": "..."} - jobs sorted by each job's best score across all
    requested profiles (descending), so the strongest match for ANY of your resumes surfaces
    first regardless of which resume produced it.

    The returned set is also never NARROWER than what was already in the database for these
    profiles: each profile's full past match history (within the last 7 days - see
    delete_stale_jobs) is merged in on top of whatever this run's own fetch produced, and any job
    this run touched but couldn't actually score gets included too, tagged match_tier="Pending"
    (see run_search_for_profile). So a search never makes an earlier run's results disappear -
    only adds to them - and "Pending" cards make an in-progress rate-limit backoff visible
    instead of just looking like a smaller result set.
    """
    search_all_companies = companies is None
    company_list = companies if companies is not None else list(get_all_companies().keys())

    merged = {}
    logs = []

    for profile_id in profile_ids:
        profile = get_profile_by_id(profile_id)
        if not profile:
            logs.append(f"[profile {profile_id}] not found - skipped.")
            continue

        if search_all_companies:
            matches, log = run_search_captured(
                profile, profile_id, title_override, location, relocation_ok,
                skip_cache=skip_cache, all_companies=True)
            matches_by_company = {"all companies": matches}
        else:
            matches_by_company = {}
            log_parts = []
            for company in company_list:
                company_matches, company_log = run_search_captured(
                    profile, profile_id, company, title_override, location, relocation_ok,
                    include_aggregators=False, skip_cache=skip_cache)
                matches_by_company[company] = company_matches
                if company_log:
                    log_parts.append(f"-- {company} --\n{company_log}")
            log = "\n".join(log_parts)

        if log:
            logs.append(f"[{profile.get('label')}]\n{log}")

        fresh_matches = [job for matches in matches_by_company.values() for job in matches]

        # Never let a job this profile has ALREADY been matched against (any past run, any
        # company) silently disappear from view just because THIS run's live/cache fetch didn't
        # happen to return it again - e.g. it dropped off a live feed, this run only covered a
        # different company, or an API hiccup returned nothing. This is what stops "click live,
        # forget to export" from actually losing anything: the old behavior only ever returned
        # matches for job urls THIS run's fetch produced, so anything not re-fetched just
        # vanished from the results list even though it was still sitting in the database.
        # delete_stale_jobs(max_age_days=7) already prunes anything with an old/unknown
        # posted_date once per app session (see streamlit_app.py's startup_cleanup_done block),
        # so everything get_matches returns here is already within that same 7-day freshness
        # window - no separate date filter needed.
        fresh_urls = {job["url"] for job in fresh_matches}
        past_matches = [m for m in get_matches(profile_id) if m["url"] not in fresh_urls]

        for job in fresh_matches + past_matches:
            url = job["url"]
            if url not in merged:
                merged[url] = {field: job.get(field) for field in _JOB_DISPLAY_FIELDS}
                merged[url]["scores"] = []
            merged[url]["scores"].append({
                "profile_id": profile_id,
                "label": profile.get("label"),
                "match_tier": job.get("match_tier"),
                "match_score": job.get("match_score"),
                "match_points": job.get("match_points"),
                "match_gaps": job.get("match_gaps"),
                "match_reasoning": job.get("match_reasoning"),
                "dimension_breakdown": job.get("dimension_breakdown"),
                "opened_at": job.get("opened_at"),
            })

    jobs = list(merged.values())
    jobs.sort(key=lambda j: max((s.get("match_score") or 0) for s in j["scores"]), reverse=True)

    return {"jobs": jobs, "log": "\n\n".join(logs)}
