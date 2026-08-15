"""Renders a normalized structured profile (see profile_extractor.py's schema) back out as a
clean, ATS-friendly PDF resume: single column, standard section headers, no tables, no
graphics, no multi-column layout - plain extractable text throughout, since that's what
actually determines whether a literal ATS parser reads a resume correctly.

Deliberately carries NO JobScout AI branding anywhere in the document - this file is meant to
be used as-is, submitted directly to an employer. Compare with pdf_export.py's match report,
which IS a JobScout AI-branded internal report and is never meant to be sent to anyone.

Why this exists at all: if match scoring is done against a normalized version of your resume,
but you apply with your original (differently-formatted, differently-worded) file, the
employer's system sees something that didn't earn the "Strong match" verdict. This closes that
gap - apply with this file, and what gets parsed on their end lines up with what generated the
assessment.
"""

from fpdf import FPDF, XPos, YPos
from pdf_export import _sanitize_for_pdf

SECTION_HEADER_SIZE = 12
BODY_SIZE = 10
LABEL_COLOR = (90, 90, 90)


class ATSResumePDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def _section_header(pdf: ATSResumePDF, title: str):
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", SECTION_HEADER_SIZE)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, _sanitize_for_pdf(title.upper()), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 190, pdf.get_y())
    pdf.ln(3)


def build_ats_resume_pdf(profile: dict, output_path: str) -> str:
    pdf = ATSResumePDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # --- Header: name + contact line ---
    full_name = profile.get("full_name") or "Resume"
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, _sanitize_for_pdf(full_name), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    contact_parts = [p for p in [profile.get("email"), profile.get("phone"),
                                  profile.get("current_location")] if p]
    if contact_parts:
        pdf.set_font("Helvetica", "", BODY_SIZE)
        pdf.set_text_color(*LABEL_COLOR)
        pdf.cell(0, 6, _sanitize_for_pdf("  |  ".join(contact_parts)),
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)

    links = profile.get("links") or []
    if links:
        link_parts = [f"{l.get('label', 'Link')}: {l.get('url', '')}" for l in links if l.get("url")]
        if link_parts:
            pdf.set_font("Helvetica", "", BODY_SIZE)
            pdf.set_text_color(*LABEL_COLOR)
            pdf.multi_cell(0, 6, _sanitize_for_pdf("  |  ".join(link_parts)),
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)

    # --- Summary ---
    summary = profile.get("summary")
    if summary:
        _section_header(pdf, "Summary")
        pdf.set_font("Helvetica", "", BODY_SIZE)
        pdf.multi_cell(0, 6, _sanitize_for_pdf(summary), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # --- Skills ---
    skills = profile.get("skills") or {}
    hands_on = skills.get("hands_on") or []
    trained = skills.get("trained") or []
    if hands_on or trained:
        _section_header(pdf, "Skills")
        pdf.set_font("Helvetica", "", BODY_SIZE)
        if hands_on:
            pdf.multi_cell(0, 6, _sanitize_for_pdf(", ".join(hands_on)),
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if trained:
            pdf.ln(1)
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*LABEL_COLOR)
            pdf.multi_cell(0, 6, _sanitize_for_pdf("Familiar with: " + ", ".join(trained)),
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)

    # --- Certifications ---
    certifications = profile.get("certifications") or []
    if certifications:
        _section_header(pdf, "Certifications")
        pdf.set_font("Helvetica", "", BODY_SIZE)
        for cert in certifications:
            name = cert.get("name") or ""
            abbreviation = cert.get("abbreviation")
            issuer = cert.get("issuing_body")
            line = name
            if abbreviation:
                line += f" ({abbreviation})"
            if issuer:
                line += f" - {issuer}"
            pdf.multi_cell(0, 6, _sanitize_for_pdf(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # --- Professional experience ---
    work_experience = profile.get("work_experience") or []
    if work_experience:
        _section_header(pdf, "Professional Experience")
        for role in work_experience:
            pdf.set_font("Helvetica", "B", BODY_SIZE)
            title_line = role.get("title") or ""
            company = role.get("company")
            if company:
                title_line += f" - {company}"
            pdf.multi_cell(0, 6, _sanitize_for_pdf(title_line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            start_date = role.get("start_date")
            end_date = role.get("end_date")
            if start_date and end_date:
                date_line = f"{start_date} - {end_date}"
            else:
                date_line = start_date or end_date or ""
            location = role.get("location")
            if location:
                date_line = f"{date_line}  |  {location}" if date_line else location
            if date_line:
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(*LABEL_COLOR)
                pdf.cell(0, 6, _sanitize_for_pdf(date_line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(0, 0, 0)

            pdf.set_font("Helvetica", "", BODY_SIZE)
            for achievement in (role.get("achievements") or []):
                pdf.multi_cell(0, 6, _sanitize_for_pdf(f"- {achievement}"),
                                new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2)

    # --- Projects ---
    projects = profile.get("projects") or []
    if projects:
        _section_header(pdf, "Projects")
        for project in projects:
            pdf.set_font("Helvetica", "B", BODY_SIZE)
            pdf.multi_cell(0, 6, _sanitize_for_pdf(project.get("name") or ""),
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", "", BODY_SIZE)
            description = project.get("description")
            if description:
                pdf.multi_cell(0, 6, _sanitize_for_pdf(description), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            skills_used = project.get("skills_used") or []
            if skills_used:
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(*LABEL_COLOR)
                pdf.multi_cell(0, 6, _sanitize_for_pdf("Skills: " + ", ".join(skills_used)),
                                new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

    # --- Education ---
    education = profile.get("education") or []
    if education:
        _section_header(pdf, "Education")
        pdf.set_font("Helvetica", "", BODY_SIZE)
        for entry in education:
            line = entry.get("degree") or ""
            institution = entry.get("institution")
            years = entry.get("years")
            if institution:
                line += f" - {institution}"
            if years:
                line += f" ({years})"
            pdf.multi_cell(0, 6, _sanitize_for_pdf(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.output(output_path)
    return output_path


if __name__ == "__main__":
    # Zero-Gemini-cost, zero-network sanity check against a hand-built fake profile - run this
    # directly to confirm the PDF layout logic itself works before ever pointing it at real
    # extracted data.
    fake_profile = {
        "full_name": "Jordan Sample",
        "email": "jordan.sample@example.com",
        "phone": "+1 555-0100",
        "current_location": "Austin, Texas",
        "links": [{"label": "LinkedIn", "url": "linkedin.com/in/jordansample"}],
        "summary": "Product manager with 6 years of experience shipping B2B SaaS features.",
        "skills": {
            "hands_on": ["Roadmapping", "SQL", "Figma", "A/B Testing"],
            "trained": ["Looker", "Segment"],
        },
        "certifications": [
            {"name": "Certified Scrum Product Owner", "abbreviation": "CSPO", "issuing_body": "Scrum Alliance"},
        ],
        "work_experience": [
            {
                "company": "Example Corp",
                "title": "Senior Product Manager",
                "location": "Austin, TX",
                "start_date": "Jan 2022",
                "end_date": "Present",
                "achievements": [
                    "Launched a self-serve onboarding flow that cut activation time by 40%.",
                    "Owned the roadmap for the billing platform across three quarters.",
                ],
            },
        ],
        "projects": [
            {"name": "Side Project X", "description": "A tool for tracking personal habits.",
             "skills_used": ["React", "Firebase"]},
        ],
        "education": [
            {"degree": "B.S. Computer Science", "institution": "University of Texas", "years": "2014-2018"},
        ],
    }

    output_path = build_ats_resume_pdf(fake_profile, "resume_builder_test.pdf")
    print(f"Test ATS resume written to {output_path} (from fake data - inspect it visually).")
