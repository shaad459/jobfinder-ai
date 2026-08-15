import json
from gemini_utils import call_gemini

EXTRACTION_PROMPT = """You are a resume parser building a NORMALIZED, structured representation of a
resume - the goal is that two very differently-formatted resumes for similar candidates should produce
directly comparable structured data. Given the raw text of a resume below, extract the following
information and return ONLY valid JSON (no markdown code fences, no extra commentary) matching this
exact structure:

{{
  "full_name": "the candidate's full name as stated on the resume, or null if not found",
  "phone": "the candidate's phone number as stated, or null if not found",
  "links": [
    {{"label": "short label for the link, e.g. 'LinkedIn', 'GitHub', 'Portfolio'", "url": "the URL as stated"}}
  ],
  "summary": "one sentence summary of the candidate",
  "current_location": "city, state/country as stated in the resume, or null if not mentioned",
  "email": "the candidate's email address as stated in the resume, or null if not found",
  "total_years_experience": 0,
  "domain": "primary industry/domain, e.g. Product Management, Software Engineering",
  "job_titles": ["most recent title", "previous title"],
  "work_experience": [
    {{
      "company": "employer name",
      "title": "job title held at this employer",
      "location": "city/region for this role, or null if not stated",
      "start_date": "as stated in the resume, e.g. 'Jul 2023' - do not reformat or guess",
      "end_date": "as stated in the resume, or 'Present' if current - do not reformat or guess",
      "achievements": ["specific bullet point or accomplishment, verbatim or close to it"]
    }}
  ],
  "skills": {{
    "hands_on": ["skills the candidate has clearly and actively APPLIED in real work - evidenced by an
      achievement bullet, a project, or explicit description of using it, not just a bare mention"],
    "trained": ["skills the resume mentions the candidate has training in, coursework for, or exposure
      to, but WITHOUT clear evidence of hands-on application in actual work or projects"]
  }},
  "certifications": [
    {{
      "name": "full certification name as stated, e.g. 'Professional Scrum Product Owner'",
      "abbreviation": "the short form if the resume states one, e.g. 'PSPO' - null if none is given",
      "issuing_body": "the certifying organization if stated, e.g. 'Scrum.org' - null if not stated"
    }}
  ],
  "projects": [
    {{
      "name": "project name/title",
      "description": "one to two sentence description of what it is/does",
      "skills_used": ["skill or technology explicitly used in this project"]
    }}
  ],
  "education": [
    {{
      "degree": "degree/diploma name as stated",
      "institution": "school/university name",
      "years": "as stated in the resume, e.g. '2015-2017' - null if not given"
    }}
  ]
}}

GROUNDING RULE: Every value must be explicitly supported by the resume text - do not infer, guess, or
invent anything that isn't actually stated. If a section (e.g. certifications, projects) genuinely isn't
present in the resume, return an empty list for it rather than fabricating entries. Do not reformat
dates into a different format than how the resume states them - copy them as written.

CERTIFICATIONS - COMPLETION STATUS: only include a certification in the "certifications" list if the
resume indicates it has actually been completed/obtained. If the resume states a certification is in
progress, pending, registered for, or expected (not yet complete) - e.g. "registered for exam, in
progress" - EXCLUDE it entirely from "certifications" rather than including it with a status note; an
incomplete certification is not yet a genuine qualification. A certification with no such qualifier is
assumed complete. This applies even if the resume also lists it as a skill elsewhere - do not add it to
"skills" either in that case, since it isn't a genuine qualification yet.

CERTIFICATIONS - NO DUPLICATION: a completed certification that appears in "certifications" should NOT
also appear in "skills.hands_on" or "skills.trained" - each item should be represented once, in whichever
list is the better fit (a formal certification belongs in "certifications", not in "skills").

Resume text:
---
{resume_text}
---
"""


def extract_structured_profile(resume_text: str) -> dict:
    prompt = EXTRACTION_PROMPT.format(resume_text=resume_text)
    response = call_gemini(prompt)
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
