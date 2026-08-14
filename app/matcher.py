import json
import os
from dotenv import load_dotenv
from google import genai

from connectors.workday_connector import fetch_workday_job_description

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

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
  or "Weak" (if the mismatch is significant) - never "Strong".
- "score": 0-100, consistent with the tier.
- "matching_points": concrete, specific things from the candidate's profile that genuinely match this
  job - cite the actual matching skill/keyword, not a vague category.
- "gaps": concrete things the job appears to require that are NOT present in the candidate's profile.
  Be specific, and flag anything that reads as non-negotiable. Empty list if there are truly no gaps.
- "reasoning": one short sentence summarizing the overall verdict.

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
    "reasoning": "short summary"
  }}
]
"""


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

    response = client.interactions.create(
        model="gemini-3.5-flash",
        input=prompt,
    )
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
    for job in jobs:
        job = dict(job)
        if job.get("source") == "workday" and not job.get("description"):
            try:
                job["description"] = fetch_workday_job_description(job)
            except Exception as e:
                print(f"Warning: could not fetch description for {job.get('title')} @ {job.get('company')}: {e}")
        enriched.append(job)
    return enriched


def score_all_jobs(profile: dict, jobs: list[dict], batch_size: int = 10) -> list[dict]:
    jobs = _enrich_missing_descriptions(jobs)
    scored_jobs = []
    for start in range(0, len(jobs), batch_size):
        batch = jobs[start:start + batch_size]
        assessments = score_jobs_batch(profile, batch)

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
            scored_jobs.append(job)

    return scored_jobs