// Thin fetch wrapper around api_server.py (FastAPI), which is a thin wrapper around the same
// repository.py/search_service.py core streamlit_app.py uses - see api_server.py's docstring.
// Change API_BASE if you run the backend on a different port.
const API_BASE = "http://localhost:8000";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: options.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // response wasn't JSON - fall back to statusText
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  health: () => request("/api/health"),

  listResumes: (activeOnly = true) => request(`/api/resumes?active_only=${activeOnly}`),
  getResume: (id) => request(`/api/resumes/${id}`),
  uploadResume: (file, label) => {
    const form = new FormData();
    form.append("file", file);
    const qs = label ? `?label=${encodeURIComponent(label)}` : "";
    return request(`/api/resumes${qs}`, { method: "POST", body: form });
  },
  patchResume: (id, patch) =>
    request(`/api/resumes/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  getResumeMatches: (id) => request(`/api/resumes/${id}/matches`),

  listCompanies: () => request("/api/companies"),
  addCompany: (name, workdayUrl) =>
    request("/api/companies", {
      method: "POST",
      body: JSON.stringify({ name, workday_url: workdayUrl }),
    }),
  removeCompany: (name) => request(`/api/companies/${encodeURIComponent(name)}`, { method: "DELETE" }),

  search: (body) => request("/api/search", { method: "POST", body: JSON.stringify(body) }),

  markOpened: (profileId, jobUrl) =>
    request("/api/mark-opened", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, job_url: jobUrl }),
    }),

  syncJobCache: () => request("/api/job-cache/sync", { method: "POST" }),
  geminiUsage: () => request("/api/gemini-usage"),
};
