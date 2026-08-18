"""Learns from your own history of already-scored jobs to avoid re-spending a Gemini call on
something this pipeline has effectively already judged before - built specifically to conserve
Gemini's rate limit (see gemini_calls / the "Gemini usage today" panel), which is the actual
bottleneck searches were hitting, not a lack of matching intelligence.

Two independent things live here, both deliberately NOT using a full LLM (local or cloud) -
comparing job text against a resume's requirements needs semantic similarity, not generation,
and an embedding/vector-similarity approach is a much lighter, cheaper, more testable fit than
running a chat model just to compare two chunks of text. This uses scikit-learn's TF-IDF +
cosine similarity rather than a neural embedding model (e.g. sentence-transformers) specifically
because TF-IDF has no GPU/torch dependency (a multi-GB install that's overkill for this) and is
precise for the near-duplicate-posting case this module cares most about; if you later want it to
also catch paraphrased-but-not-textually-similar matches (e.g. "stakeholder management" vs
"cross-functional leadership"), swapping in a real embedding model is a contained change scoped
to _fit_similarity_matrix() below, nothing else would need to change.

1. Near-duplicate reuse (find_history_action, action="reuse"): the same posting often shows up
   more than once - re-posted later, or surfaced again via a different source (Workday directly
   vs. a JSearch/Adzuna aggregate of the same listing under a different URL). Plain URL-based
   dedup (repository.get_scored_job_urls) can't catch that. Two ways in:
     a. EXACT match on external_id (the source ATS's own stable job/requisition id - see
        database.py's comment on the jobs table and each connectors/*_connector.py) - checked
        FIRST, before any text comparison, since it's a guaranteed identity rather than a
        similarity guess: the same Workday requisition, Greenhouse/Lever job id, Oracle
        requisition, or Avature posting resurfacing under a different url (a title edit changed
        Avature's slug, a tracking query param got added) is unambiguously the same job. Skips
        the TF-IDF step entirely when it fires.
     b. Near-exact textual similarity (>= EXACT_DUP_THRESHOLD) to something ALREADY Gemini-scored
        for this exact resume AND at the same company - the original, similarity-based fallback
        for everything without a usable external_id (an older connector, JSearch/Adzuna, or
        extraction that came back empty). Restricted to the same company (not just similar text)
        specifically to avoid two different companies' independently-written, coincidentally-
        similar-sounding JDs getting treated as "the same job" - company/location specifics
        wouldn't necessarily transfer.
   Either way, the verdict is reused outright and Gemini is skipped entirely for that job.

2. Weak-pattern pre-exclusion (find_history_action, action="skip_weak"): a new job that's
   MODERATELY similar (WEAK_REUSE_THRESHOLD - EXACT_DUP_THRESHOLD) to a job that already scored
   Weak for a candidate-intrinsic reason (a missing skill/certification/domain/experience gap -
   something about the CANDIDATE, not the specific employer) is treated as pre-filtered-out,
   skipping Gemini, on the reasoning that the same gap almost certainly still applies. This is
   DELIBERATELY ONE-DIRECTIONAL - moderate similarity to a past WEAK match skips Gemini, but
   moderate similarity to a past STRONG/GOOD match never auto-assigns that tier without Gemini
   actually checking. A false "skip, this is probably still a gap" is a small, recoverable error
   (search live / re-check if you disagree); a false "claim this is a good match" without
   verification would undermine the one thing this whole app promises - grounded, checked
   verdicts, not assumptions. Never applied when the past match's ONLY apparent gap was location
   or role (see _is_candidate_intrinsic_gap) - those genuinely differ posting to posting and
   aren't safe to transfer.

Every job produced by either path is tagged in match_reasoning with a "(...)" prefix explaining
it was inferred from history, not fresh-checked - same transparency convention matcher.py's
existing SCREENED_OUT_PLACEHOLDER/_prefiltered_placeholder already use for Stage 0 exclusions.
A tagged entry is also never itself usable as a future reuse/skip source (see
_is_usable_as_history_source) - only genuinely Gemini-verified verdicts can be propagated
forward, so an inferred verdict can never drift further from what Gemini actually said.
"""
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

EXACT_DUP_THRESHOLD = 0.93   # near-identical text -> safe to reuse the verdict outright
WEAK_REUSE_THRESHOLD = 0.85  # similar enough to a past Weak match -> safe to skip, not to reuse

HISTORY_INFERRED_MARKER = "(Inferred from a similar posting already checked for this resume - "

_DIMENSIONS_SAFE_TO_TRANSFER = {"skills", "certification", "experience", "domain"}


def _normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_usable_as_history_source(match: dict) -> bool:
    """Only a verdict Gemini actually produced can be used as a source for a future reuse/skip
    decision - excludes prior Stage 0 pre-filter placeholders, prescreen rejects, AND anything
    this same module previously tagged as inferred, so an inference can never be built on top of
    an earlier inference (no drift-compounding across searches).
    """
    reasoning = match.get("match_reasoning") or ""
    if reasoning.startswith(HISTORY_INFERRED_MARKER):
        return False
    # Stage 0/prescreen placeholders (matcher.py's SCREENED_OUT_PLACEHOLDER /
    # _prefiltered_placeholder) always set dimension_breakdown to {} - a real Gemini Stage 2
    # verdict always has all six dimensions populated.
    return bool(match.get("dimension_breakdown"))


def _is_candidate_intrinsic_gap(dimension_breakdown: dict) -> bool:
    """True only if every dimension that ISN'T a clear match is one of the candidate-intrinsic
    ones (skills/certification/experience/domain) - i.e. nothing about location or role is what
    made this a Weak match. Those two are posting-specific and don't safely transfer to a
    different (even if textually similar) job.
    """
    if not dimension_breakdown:
        return False
    for dimension, detail in dimension_breakdown.items():
        level = (detail or {}).get("level")
        if level and level != "match" and dimension not in _DIMENSIONS_SAFE_TO_TRANSFER:
            return False
    return True


def _fit_similarity_matrix(new_description: str, history_descriptions: list[str]):
    """Fits a fresh TF-IDF vectorizer over [new_description] + history each call rather than
    persisting one - history per resume is realistically dozens to low hundreds of jobs, so
    refitting is cheap (milliseconds), and it sidesteps having to keep a fitted vectorizer's
    vocabulary in sync with a growing, changing corpus across sessions.
    """
    corpus = [new_description] + history_descriptions
    vectorizer = TfidfVectorizer(stop_words="english", max_features=4096)
    matrix = vectorizer.fit_transform(corpus)
    # Row 0 is the new job; compare it against every history row.
    return cosine_similarity(matrix[0:1], matrix[1:])[0]


def find_history_action(new_job: dict, history: list[dict]) -> dict | None:
    """new_job: dict with at least 'title', 'company', 'description' - and, when the connector
        could extract one, 'external_id' (see database.py's comment on the jobs table).
    history: this profile's own past matches, each a dict with 'title', 'company', 'description',
        'match_tier', 'match_score', 'match_points', 'match_gaps', 'match_reasoning',
        'dimension_breakdown', 'url', 'external_id' - i.e. repository.get_matches(profile_id)'s
        shape (get_matches selects jobs.* via its JOIN, so external_id comes along for free).

    Returns None if no history action applies (falls through to the normal Stage 0/1/2 pipeline
    unchanged), or a dict: {"action": "reuse"|"skip_weak", "source": <history entry>,
    "similarity": float}.
    """
    usable_history = [h for h in history if _is_usable_as_history_source(h)]
    if not usable_history:
        return None

    # Exact external_id match - a guaranteed identity, not a similarity guess, so it's checked
    # first and skips the TF-IDF step entirely. See the module docstring's point 1a.
    new_external_id = new_job.get("external_id")
    if new_external_id:
        for h in usable_history:
            if h.get("external_id") == new_external_id:
                return {"action": "reuse", "source": h, "similarity": 1.0}

    new_description = _normalize_text(new_job.get("description"))
    if not new_description or len(new_description) < 50:
        # Too little text for TF-IDF similarity to mean anything - fail open, same philosophy
        # as every other filter in this pipeline (matcher.py's Stage 0 checks).
        return None

    usable_history = [h for h in usable_history if h.get("description")]
    if not usable_history:
        return None

    history_descriptions = [_normalize_text(h["description"]) for h in usable_history]
    similarities = _fit_similarity_matrix(new_description, history_descriptions)

    best_idx = similarities.argmax()
    best_similarity = similarities[best_idx]
    best_match = usable_history[best_idx]

    same_company = (new_job.get("company") or "").strip().lower() == \
        (best_match.get("company") or "").strip().lower()

    if best_similarity >= EXACT_DUP_THRESHOLD and same_company:
        return {"action": "reuse", "source": best_match, "similarity": float(best_similarity)}

    if (WEAK_REUSE_THRESHOLD <= best_similarity < EXACT_DUP_THRESHOLD
            and best_match.get("match_tier") in ("Weak", None)
            and _is_candidate_intrinsic_gap(best_match.get("dimension_breakdown") or {})):
        return {"action": "skip_weak", "source": best_match, "similarity": float(best_similarity)}

    return None


def apply_reuse(job: dict, action: dict) -> dict:
    """Builds the scored-job dict for a "reuse" action - copies the source verdict (including
    its real dimension_breakdown, since it reflects an actually-Gemini-checked near-identical
    posting) onto the new job, tagged as inferred. action["similarity"] == 1.0 specifically means
    an exact external_id match (see find_history_action) rather than a genuine 100% text-
    similarity score - worded accordingly below rather than reporting a slightly misleading
    "100% text match".
    """
    source = action["source"]
    job = dict(job)
    job["match_tier"] = source.get("match_tier")
    job["match_score"] = source.get("match_score")
    job["match_points"] = source.get("match_points") or []
    job["match_gaps"] = source.get("match_gaps") or []
    job["dimension_breakdown"] = source.get("dimension_breakdown") or {}
    if action["similarity"] >= 1.0:
        basis = "confirmed same posting (matching job id) as one already scored"
    else:
        pct = round(action["similarity"] * 100)
        basis = f"{pct}% text match to an already-scored posting at the same company"
    job["match_reasoning"] = (
        f"{HISTORY_INFERRED_MARKER}{basis}): {source.get('match_reasoning') or ''}"
    )
    return job


def apply_skip_weak(job: dict, action: dict) -> dict:
    """Builds the scored-job dict for a "skip_weak" action - does NOT copy the source's
    dimension_breakdown (that was a different posting; we're inferring the gap likely still
    applies, not claiming to have re-verified all six dimensions for THIS one), so it's left
    empty like any other pre-Gemini exclusion in this pipeline.
    """
    source = action["source"]
    pct = round(action["similarity"] * 100)
    job = dict(job)
    job["match_tier"] = "Weak"
    job["match_score"] = 0
    job["match_points"] = []
    job["match_gaps"] = source.get("match_gaps") or []
    job["dimension_breakdown"] = {}
    job["match_reasoning"] = (
        f"{HISTORY_INFERRED_MARKER}{pct}% text match to a posting already scored Weak for the "
        f"same reason - not independently re-checked by Gemini): {source.get('match_reasoning') or ''}"
    )
    return job
