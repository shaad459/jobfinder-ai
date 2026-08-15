"""Generates a per-job "tailored" variant of a candidate's normalized profile, for use with a
specific Strong match - reflecting that job's own terminology and leading with the most relevant
existing content, so what gets submitted lines up more closely with how that employer's own
screen (human or automated) is likely to read it.

This is deliberately built on-demand (one Gemini call per job you choose to tailor for), not
automatically for every Strong match - see the trigger-mode discussion in chat. Every match
already found its tier/score before this ever runs; tailoring doesn't change or re-score the
match, it only repackages the resume that would go with an application to that one job.

GROUNDING is enforced twice: once by prompt instruction (same discipline as profile_extractor.py
and matcher.py), and again in code via _tailored_profile_from_response() below, which validates
that skills/certifications are true reorderings (not additions/removals/moves-between-buckets)
and that achievement counts per role are unchanged before accepting any tailored field. Any field
that fails validation silently falls back to the original, untailored version of that field
rather than either crashing or trusting an ungrounded model response.
"""

import json
import re
from pathlib import Path
from gemini_utils import call_gemini

TAILOR_PROMPT_TEMPLATE = """You are tailoring a candidate's resume content for ONE specific job posting, using
only facts already present in their profile - the goal is to mirror this job's own terminology and highlight the
most relevant existing content, NOT to add anything new.

Candidate's normalized profile:
{profile_json}

Target job:
Title: {job_title}
Company: {job_company}
Description:
{job_description}

This candidate's assessed fit against this job (context on what to emphasize - do not "fix" any listed gap by
inventing content; gaps that reflect real missing experience must stay honestly absent):
{dimension_breakdown_json}

GROUNDING RULE (critical, absolute): You may REORDER and REWORD existing content to better mirror this job's
terminology and highlight relevance. You may NEVER add a skill, certification, achievement, employer, title,
date, or any other fact that is not already present in the candidate's profile above. Do not move a skill
between "hands_on" and "trained" - that distinction reflects real proficiency level and must not change. Do not
alter company names, job titles, locations, or dates in any way.

Return ONLY valid JSON (no markdown fences, no extra text) in this exact shape:
{{
  "tailored_summary": "one sentence summary, reworded to mirror this job's language, still 100% grounded in the
    original summary/profile - no new claims",
  "skills_hands_on_order": ["the exact same items as the candidate's skills.hands_on list, just reordered so the
    most relevant-to-this-job items come first - do not add, remove, or reword any item"],
  "skills_trained_order": ["the exact same items as the candidate's skills.trained list, reordered the same way"],
  "certifications_order": ["the exact same certification names as the candidate's certifications list (their
    \\"name\\" field), reordered so the most relevant-to-this-job ones come first"],
  "work_experience": [
    {{
      "company": "must exactly match a company from the candidate's work_experience",
      "achievements": ["the same NUMBER of achievements as the original entry for this company, each one
        reworded (never fabricated) to use this job's terminology where a genuine equivalent exists, and
        reordered so the most relevant ones lead - never invent a new achievement or change the count"]
    }}
  ]
}}
"""


def _safe_filename_part(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", text or "").strip()


def tailored_resume_filename(original_resume_path: str, company: str) -> str:
    stem = Path(original_resume_path).stem
    safe_company = _safe_filename_part(company) or "Employer"
    return f"{stem} - {safe_company} ATS.pdf"


def _tailored_profile_from_response(profile: dict, tailored: dict) -> dict:
    # Deep copy via JSON round-trip - safe since a normalized profile is plain JSON-serializable
    # data throughout (str/int/float/bool/None/list/dict), same as everywhere else it's handled.
    result = json.loads(json.dumps(profile))

    tailored_summary = tailored.get("tailored_summary")
    if isinstance(tailored_summary, str) and tailored_summary.strip():
        result["summary"] = tailored_summary.strip()

    original_skills = profile.get("skills") or {}
    result.setdefault("skills", {"hands_on": [], "trained": []})
    for field, tailored_key in (("hands_on", "skills_hands_on_order"), ("trained", "skills_trained_order")):
        original_list = original_skills.get(field) or []
        proposed = tailored.get(tailored_key)
        if isinstance(proposed, list) and sorted(proposed) == sorted(original_list):
            result["skills"][field] = proposed
        elif proposed is not None:
            print(f"Note: tailoring proposed a changed skills.{field} list (not just reordered) - "
                  f"keeping your original order instead.")

    original_certs = profile.get("certifications") or []
    original_cert_names = [c.get("name") for c in original_certs]
    proposed_cert_order = tailored.get("certifications_order")
    if isinstance(proposed_cert_order, list) and sorted(proposed_cert_order) == sorted(original_cert_names):
        certs_by_name = {c.get("name"): c for c in original_certs}
        result["certifications"] = [certs_by_name[name] for name in proposed_cert_order]
    elif proposed_cert_order is not None:
        print("Note: tailoring proposed a changed certifications list (not just reordered) - "
              "keeping your original order instead.")

    original_roles_by_company = {role.get("company"): role for role in (profile.get("work_experience") or [])}
    tailored_achievements_by_company = {}
    for proposed_role in (tailored.get("work_experience") or []):
        company = proposed_role.get("company")
        achievements = proposed_role.get("achievements")
        original_role = original_roles_by_company.get(company)
        if not original_role:
            continue
        original_achievements = original_role.get("achievements") or []
        if isinstance(achievements, list) and len(achievements) == len(original_achievements):
            tailored_achievements_by_company[company] = achievements
        else:
            print(f"Note: tailoring proposed a different number of achievements for {company} - "
                  f"keeping your original achievements for that role.")

    new_work_experience = []
    for role in (profile.get("work_experience") or []):
        new_role = dict(role)
        company = role.get("company")
        if company in tailored_achievements_by_company:
            new_role["achievements"] = tailored_achievements_by_company[company]
        new_work_experience.append(new_role)
    result["work_experience"] = new_work_experience

    return result


def tailor_profile_for_job(profile: dict, job: dict) -> dict:
    """Returns a new profile dict (same schema as the normalized profile) with summary, skill/
    certification ordering, and per-role achievement wording/ordering adjusted for this one job -
    every other field (name, contact info, dates, companies, titles, education, projects) is
    passed through unchanged. Falls back field-by-field to the original on any validation
    failure, and falls back to the entire original profile if the model response isn't even
    parseable JSON, so a bad tailoring response never blocks you from still having a usable
    (untailored) ATS resume for that job.
    """
    dimension_breakdown = job.get("dimension_breakdown") or {}
    prompt = TAILOR_PROMPT_TEMPLATE.format(
        profile_json=json.dumps(profile, indent=2),
        job_title=job.get("title") or "",
        job_company=job.get("company") or "",
        job_description=(job.get("description") or "")[:3000],
        dimension_breakdown_json=json.dumps(dimension_breakdown, indent=2),
    )

    response = call_gemini(prompt, model="gemini-3.5-flash")
    raw_output = response.output_text.strip()
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        raw_output = raw_output.replace("json\n", "", 1)

    try:
        tailored = json.loads(raw_output)
    except json.JSONDecodeError:
        print("Could not parse tailoring JSON - falling back to your untailored profile for this job.")
        print(raw_output)
        return json.loads(json.dumps(profile))

    return _tailored_profile_from_response(profile, tailored)
