import { useRef, useState } from "react";
import { api } from "../api";

// The core of the multi-resume feature: every resume you've ever uploaded (Business Analyst,
// Product Owner, Project Manager, ...) lives here permanently, keyed by content hash on the
// backend (see repository.get_or_create_profile) - re-uploading the same file again is instant
// and free. "Retire" archives a resume (excludes it from search) without deleting its match
// history; it can be restored any time. Selection here (via checkboxes) feeds directly into
// SearchPanel's "search as" list.
export default function ResumeLibrary({ resumes, selectedIds, onToggleSelect, onRefresh }) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [labelDraft, setLabelDraft] = useState("");
  const [editingId, setEditingId] = useState(null);
  const fileInputRef = useRef(null);

  async function handleFileChosen(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const result = await api.uploadResume(file);
      onRefresh();
      onToggleSelect(result.profile.id, true);
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function saveLabel(id) {
    if (labelDraft.trim()) {
      await api.patchResume(id, { label: labelDraft.trim() });
      onRefresh();
    }
    setEditingId(null);
  }

  async function toggleActive(resume) {
    await api.patchResume(resume.id, { active: !resume.active });
    onRefresh();
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Resume library</h2>
        <p className="muted">
          Keep one resume per role you target - Business Analyst, Product Owner, Project
          Manager, whatever you've tailored. Check the ones you want included in your next
          search below.
        </p>
      </div>

      <label className="upload-dropzone">
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx"
          onChange={handleFileChosen}
          disabled={uploading}
          hidden
        />
        {uploading ? "Uploading and parsing with Gemini…" : "+ Add a resume (.pdf or .docx)"}
      </label>
      {uploadError && <div className="error-text">{uploadError}</div>}

      {resumes.length === 0 ? (
        <p className="muted" style={{ marginTop: "1rem" }}>
          No resumes saved yet - add your first one above.
        </p>
      ) : (
        <ul className="resume-list">
          {resumes.map((r) => (
            <li key={r.id} className={`resume-card ${r.active ? "" : "resume-card-inactive"}`}>
              <label className="resume-card-select">
                <input
                  type="checkbox"
                  checked={selectedIds.has(r.id)}
                  disabled={!r.active}
                  onChange={(e) => onToggleSelect(r.id, e.target.checked)}
                />
              </label>

              <div className="resume-card-body">
                {editingId === r.id ? (
                  <input
                    autoFocus
                    className="label-input"
                    value={labelDraft}
                    onChange={(e) => setLabelDraft(e.target.value)}
                    onBlur={() => saveLabel(r.id)}
                    onKeyDown={(e) => e.key === "Enter" && saveLabel(r.id)}
                  />
                ) : (
                  <div
                    className="resume-card-label"
                    title="Click to rename"
                    onClick={() => {
                      setEditingId(r.id);
                      setLabelDraft(r.label || "");
                    }}
                  >
                    {r.label || "(untitled resume)"}
                    {!r.active && <span className="pill pill-muted">retired</span>}
                  </div>
                )}
                <div className="resume-card-meta">
                  {(r.job_titles || []).slice(0, 3).join(" · ") || "No titles extracted"}
                  {r.total_years_experience != null && ` · ${r.total_years_experience} yrs`}
                  {r.domain && ` · ${r.domain}`}
                </div>
                {r.resume_filename && <div className="resume-card-filename">{r.resume_filename}</div>}
              </div>

              <button className="btn-ghost" onClick={() => toggleActive(r)}>
                {r.active ? "Retire" : "Restore"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
