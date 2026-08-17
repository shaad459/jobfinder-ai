"""Deterministic grounding for a specific problem: job descriptions often use GENERIC
certification phrasing ("agile certification", "cloud certification", "security certification")
instead of naming a specific credential, while a candidate's own profile only ever lists the
SPECIFIC certification they actually hold (e.g. "PSPO", "AWS Certified Solutions Architect").
Left alone, that's a synonym-recognition problem punted entirely to Gemini's own judgment -
matcher.py's GROUNDING RULE already asks it to recognize "a clear, unambiguous synonym," but
that's exactly the kind of inference that can go either way. This module makes the mapping
explicit and deterministic instead, and feeds it into the Stage 2 prompt as a per-job hint - it
does NOT skip, pre-judge, or auto-credit the certification dimension itself; Gemini still makes
the actual call, this just makes sure it's starting from the same specific-to-generic mapping a
human recruiter would already know, rather than reconstructing it from scratch for every job.

Deliberately narrow in scope: this only covers CERTIFICATION phrasing, not skills or domain
generally. Certifications are a closed, well-known vocabulary where a hand-maintained alias table
stays accurate and is cheap to extend; open-ended skill/domain language doesn't have that
property - a table there would either be huge or constantly stale, which is why
job_similarity.py's TF-IDF approach (not a lookup table) is the right tool for that broader case
instead.

Covers several role families' certification vocabularies, not just one, since this project may
be used by more than one kind of candidate (see the "would a software engineer get results"
discussion this table grew out of) - project/program management, agile/scrum/product, business
analysis, cloud (AWS/Azure/GCP), data & analytics, cybersecurity, DevOps/infrastructure, IT
service management & quality, HR, and finance/accounting. This is a representative set, not
exhaustive - extend it the same way for any category not covered yet.

MATCHING DIRECTION - deliberately ONE-WAY: a canonical key is treated as held only when it
appears as a literal substring of what the candidate actually wrote (`key in held_norm`), never
the reverse. An earlier version of this table also checked `held_norm in key`, meant to handle a
candidate who wrote only a bare short form - but once this table grew to include longer,
multi-word canonical keys (e.g. "aws certified solutions architect"), that reverse check meant a
candidate who vaguely wrote just "AWS" (with no specific certification named) would match EVERY
AWS-prefixed key. Matching this codebase's existing "grounded, never assume" philosophy, this
table now fails open instead: a vague or partial credential mention doesn't get credited, and
picking canonical keys as the short/common form actual resumes use (acronyms for the
Scrum-family certs, fuller official names for cloud certs, since that's how each is usually
written in practice) is what keeps recall reasonable without that risk.

Extending this: add a new canonical key below (in whatever form candidates actually tend to
write it) with the generic phrases it should satisfy.
"""
import re

CERTIFICATION_ALIASES = {
    # --- Project & program management ---
    "pmp": ["project management certification", "pm certification", "project management professional"],
    "prince2": ["project management certification", "pm certification"],
    "capm": ["project management certification", "pm certification", "associate project management certification"],
    "pgmp": ["program management certification", "program manager certification"],

    # --- Agile / Scrum / product ---
    "pspo": ["agile certification", "scrum certification", "product certification",
              "product management certification", "product owner certification",
              "certified product owner", "agile product owner certification"],
    "cspo": ["agile certification", "scrum certification", "product certification",
              "product management certification", "product owner certification",
              "certified product owner", "agile product owner certification"],
    "csm": ["agile certification", "scrum certification", "scrum master certification",
             "certified scrum professional"],
    "psm": ["agile certification", "scrum certification", "scrum master certification",
             "certified scrum professional"],
    "safe agilist": ["agile certification", "scaled agile certification", "safe certification"],
    "safe practitioner": ["agile certification", "scaled agile certification", "safe certification"],
    "pmi-acp": ["agile certification", "agile practitioner certification"],
    "icp-acc": ["agile certification", "agile coaching certification"],

    # --- Business analysis ---
    "cbap": ["business analysis certification", "ba certification"],
    "pmi-pba": ["business analysis certification", "ba certification"],
    "ccba": ["business analysis certification", "ba certification"],
    "ecba": ["business analysis certification", "ba certification", "entry certificate in business analysis"],

    # --- Cloud (AWS / Azure / GCP) ---
    "aws certified solutions architect": ["cloud certification", "aws certification", "solutions architect certification"],
    "aws certified developer": ["cloud certification", "aws certification", "developer certification"],
    "aws certified sysops administrator": ["cloud certification", "aws certification", "sysops certification"],
    "aws certified devops engineer": ["cloud certification", "aws certification", "devops certification"],
    "azure administrator": ["cloud certification", "azure certification"],
    "azure developer": ["cloud certification", "azure certification"],
    "azure solutions architect": ["cloud certification", "azure certification", "solutions architect certification"],
    "google cloud professional cloud architect": ["cloud certification", "gcp certification", "google cloud certification"],
    "google associate cloud engineer": ["cloud certification", "gcp certification", "google cloud certification"],

    # --- Data & analytics ---
    "power bi data analyst associate": ["data analytics certification", "power bi certification", "business intelligence certification"],
    "google data analytics": ["data analytics certification"],
    "aws certified data analytics": ["cloud certification", "data analytics certification", "aws certification"],
    "certified analytics professional": ["data analytics certification", "analytics certification"],

    # --- Cybersecurity ---
    "cissp": ["cybersecurity certification", "security certification", "information security certification"],
    "comptia security+": ["cybersecurity certification", "security certification"],
    "ceh": ["cybersecurity certification", "ethical hacking certification", "security certification"],
    "cism": ["cybersecurity certification", "security certification", "information security management certification"],
    "cisa": ["cybersecurity certification", "security certification", "information systems audit certification"],

    # --- DevOps / infrastructure ---
    "certified kubernetes administrator": ["devops certification", "kubernetes certification", "container orchestration certification"],
    "docker certified associate": ["devops certification", "docker certification", "containerization certification"],
    "terraform associate": ["devops certification", "infrastructure as code certification", "terraform certification"],
    "ccna": ["networking certification", "cisco certification"],

    # --- IT service management & quality ---
    "itil foundation": ["it service management certification", "itil certification"],
    "six sigma green belt": ["process improvement certification", "six sigma certification", "quality certification"],
    "six sigma black belt": ["process improvement certification", "six sigma certification", "quality certification"],

    # --- HR ---
    "shrm-cp": ["hr certification", "human resources certification"],
    "phr": ["hr certification", "human resources certification"],

    # --- Finance & accounting ---
    "cfa": ["finance certification", "chartered financial analyst certification", "investment certification"],
    "cpa": ["accounting certification", "finance certification"],
    "frm": ["risk management certification", "finance certification"],
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def build_certification_grounding(candidate_certifications: list, job_description: str) -> str | None:
    """Returns a short hint string for ONE job's prompt entry, or None if nothing in the
    candidate's certifications maps to generic phrasing actually present in THIS job's
    description - so jobs that don't need the hint don't carry a useless empty note, and the
    prompt only grows for the jobs where it's actually relevant.
    """
    if not candidate_certifications or not job_description:
        return None

    description_norm = _normalize(job_description)
    matches = []  # list of (held_cert_as_written, [matched generic phrases])

    for held in candidate_certifications:
        held_norm = _normalize(held)
        if not held_norm:
            continue
        # One-way match only - see the module docstring's "MATCHING DIRECTION" section for why
        # the reverse check (a short/vague candidate entry matching INTO a longer key) was
        # removed once this table grew multi-word keys.
        canonical_key = next((key for key in CERTIFICATION_ALIASES if key in held_norm), None)
        if not canonical_key:
            continue

        matched_phrases = sorted(set(
            phrase for phrase in CERTIFICATION_ALIASES[canonical_key]
            if phrase in description_norm
        ))
        if matched_phrases:
            matches.append((held, matched_phrases))

    if not matches:
        return None

    lines = [
        "CERTIFICATION GROUNDING (deterministic, not inferred): the candidate holds the "
        "certification(s) below, which should be treated as satisfying the generic phrasing "
        "from this job's own description that's listed next to each - do not count these as a "
        "certification gap on that basis alone:",
    ]
    for held, phrases in matches:
        phrase_list = ", ".join(f'"{p}"' for p in phrases)
        lines.append(f'- Candidate holds "{held}", which satisfies: {phrase_list}')

    return "\n".join(lines)
