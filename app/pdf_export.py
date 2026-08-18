from datetime import datetime, timezone
from fpdf import FPDF, XPos, YPos


def _ats_coverage_pct(job: dict) -> int | None:
    """Same free keyword-coverage ratio streamlit_app.py shows on each job card (BUILD_PLAN.md
    item 7b) - kept here too so the PDF report (and therefore the emailed digest, which reuses
    this same function) shows the identical number rather than a UI-only stat. Returns None for
    a job that never reached precise scoring - nothing to compute a ratio from.
    """
    points = job.get("match_points") or []
    gaps = job.get("match_gaps") or []
    total = len(points) + len(gaps)
    if total == 0:
        return None
    return round(100 * len(points) / total)


def _sanitize_for_pdf(text: str) -> str:
    """Core PDF fonts (Helvetica) only support latin-1 - swap the Unicode punctuation LLM
    output commonly uses for ASCII equivalents rather than crashing on it. Doesn't handle
    actual non-Latin script (e.g. Chinese/Hindi company names) - that would need a bundled
    Unicode font instead, not worth the extra asset for now given today's data is all
    English-language.
    """
    if not text:
        return text
    replacements = {
        "—": "-", "–": "-", "‘": "'", "’": "'",
        "“": '"', "”": '"', "…": "...", " ": " ", "•": "-",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", errors="replace").decode("latin-1")


class MatchReportPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 10, "JobScout AI - Match Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(120, 120, 120)
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.cell(0, 6, f"Generated {generated}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(0, 0, 0)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def export_matches_to_pdf(matches: list[dict], output_path: str, tiers: tuple = ("Strong", "Good")) -> str | None:
    """Writes a PDF of Strong/Good matches to output_path and returns that path.

    If there's nothing to export, no file is written at all and this returns None. This is
    a deliberate contract, not just a guard clause: a "no matches" PDF is bad UX (a download
    button that opens a document telling you there's nothing to see). The caller - today
    the __main__ block below, eventually a Streamlit UI - is expected to check the return
    value and only offer/show the download when it's a real path. An empty-state message
    belongs in the UI (e.g. "No Strong or Good matches yet - run a search"), not baked into
    a PDF file.
    """
    filtered = [m for m in matches if m.get("match_tier") in tiers]

    if not filtered:
        return None

    pdf = MatchReportPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    for job in filtered:
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, _sanitize_for_pdf(job.get("title") or "(untitled role)"),
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(80, 80, 80)
        location = job.get("location") or "location not listed"
        pdf.cell(0, 6, _sanitize_for_pdf(f"{job.get('company', '')} - {location}"),
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)

        tier = job.get("match_tier", "")
        score = job.get("match_score", "")
        coverage = _ats_coverage_pct(job)
        coverage_suffix = f" - ATS coverage {coverage}%" if coverage is not None else ""
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, f"{tier} match - score {score}{coverage_suffix}",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Only present when run_scheduled_search.py searched more than one resume - see
        # email_sender._build_html_body's matching comment.
        resume_label = job.get("resume_label")
        if resume_label:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(120, 120, 120)
            pdf.cell(0, 5, _sanitize_for_pdf(f"Matched as: {resume_label}"),
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)

        match_points = job.get("match_points") or []
        if match_points:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, _sanitize_for_pdf("Matches: " + ", ".join(match_points)),
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        match_gaps = job.get("match_gaps") or []
        if match_gaps:
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, _sanitize_for_pdf("Gaps: " + ", ".join(match_gaps)),
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # Six-dimension breakdown, if this job actually went through precise scoring - jobs
        # excluded by the pre-filter or the prescreen never get one (empty dict), and are
        # skipped here rather than printing an empty/misleading line.
        breakdown = job.get("dimension_breakdown") or {}
        if breakdown:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 5, "Fit breakdown:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", "", 9)
            for dimension in ("role", "location", "skills", "certification", "experience", "domain"):
                dim = breakdown.get(dimension) or {}
                level = dim.get("level")
                if not level:
                    continue
                note = dim.get("note")
                line = f"  {dimension.capitalize()}: {level.capitalize()}"
                if note:
                    line += f" - {note}"
                pdf.multi_cell(0, 5, _sanitize_for_pdf(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)

        posted = job.get("posted_date")
        if posted:
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(120, 120, 120)
            pdf.cell(0, 6, _sanitize_for_pdf(f"Posted: {posted}"),
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)

        url = job.get("url")
        if url:
            pdf.set_font("Helvetica", "U", 10)
            pdf.set_text_color(37, 99, 235)
            pdf.cell(0, 6, "View job posting", new_x=XPos.LMARGIN, new_y=YPos.NEXT, link=url)
            pdf.set_text_color(0, 0, 0)

        pdf.ln(6)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 190, pdf.get_y())
        pdf.ln(6)

    pdf.output(output_path)
    return output_path


if __name__ == "__main__":
    from database import init_db
    from repository import get_matches

    init_db()
    matches = get_matches(1)
    output_path = export_matches_to_pdf(matches, "match_report_test.pdf")
    if output_path:
        print(f"Exported (Strong/Good only) to {output_path}")
    else:
        print("No Strong or Good matches to export yet - nothing written. "
              "This is what a UI would use to decide whether to show the download button.")
