import { useState } from "react";
import { api } from "../api";
import JobCard from "./JobCard";
import CompanyManager from "./CompanyManager";

export default function SearchPanel({ resumes, selectedIds, companies, onRefreshCompanies }) {
  const [searchAllCompanies, setSearchAllCompanies] = useState(true);
  const [selectedCompanies, setSelectedCompanies] = useState(new Set());
  const [titleOverride, setTitleOverride] = useState("");
  const [location, setLocation] = useState("");
  const [relocationOk, setRelocationOk] = useState(false);
  const [skipCache, setSkipCache] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);
  const [log, setLog] = useState("");
  const [showLog, setShowLog] = useState(false);

  const selectedResumes = resumes.filter((r) => selectedIds.has(r.id));

  function toggleCompany(name, checked) {
    setSelectedCompanies((prev) => {
      const next = new Set(prev);
      if (checked) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  async function handleSearch() {
    if (selectedIds.size === 0) {
      setError("Select at least one resume in the library above first.");
      return;
    }
    if (!searchAllCompanies && selectedCompanies.size === 0) {
      setError("Pick at least one company, or switch to \"search all configured companies.\"");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const body = {
        profile_ids: Array.from(selectedIds),
        companies: searchAllCompanies ? null : Array.from(selectedCompanies),
        title_override: titleOverride.trim() || null,
        location: location.trim(),
        relocation_ok: relocationOk,
        skip_cache: skipCache,
      };
      const data = await api.search(body);
      setResults(data.jobs);
      setLog(data.log || "");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Search</h2>
        <p className="muted">
          {selectedResumes.length === 0
            ? "No resumes selected - check some in the library above."
            : `Searching as: ${selectedResumes.map((r) => r.label).join(", ")}`}
        </p>
      </div>

      <div className="search-form">
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={searchAllCompanies}
            onChange={(e) => setSearchAllCompanies(e.target.checked)}
          />
          Search all configured companies
        </label>

        {!searchAllCompanies && (
          <div className="company-checkbox-grid">
            {Object.keys(companies)
              .sort()
              .map((name) => (
                <label key={name} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={selectedCompanies.has(name)}
                    onChange={(e) => toggleCompany(name, e.target.checked)}
                  />
                  {name}
                </label>
              ))}
          </div>
        )}

        <div className="form-grid">
          <label>
            Role override (optional)
            <input
              placeholder="e.g. Business Analyst - leave blank to use each resume's own titles"
              value={titleOverride}
              onChange={(e) => setTitleOverride(e.target.value)}
            />
          </label>
          <label>
            Location
            <input
              placeholder="e.g. Pune, India"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
            />
          </label>
        </div>

        <label className="checkbox-row">
          <input type="checkbox" checked={relocationOk} onChange={(e) => setRelocationOk(e.target.checked)} />
          I'm open to relocating (skip location filtering)
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={skipCache} onChange={(e) => setSkipCache(e.target.checked)} />
          Search live instead of using the ~12h job cache
        </label>

        <CompanyManager companies={companies} onRefresh={onRefreshCompanies} />

        <button className="btn-primary btn-large" onClick={handleSearch} disabled={loading}>
          {loading ? "Searching and scoring…" : "Run search"}
        </button>
        {error && <div className="error-text">{error}</div>}
      </div>

      {results && (
        <div className="results-section">
          <div className="results-heading">
            <h3>{results.length} job{results.length === 1 ? "" : "s"} found</h3>
            {log.trim() && (
              <button className="btn-ghost btn-small" onClick={() => setShowLog((s) => !s)}>
                {showLog ? "Hide search log" : "Show search log"}
              </button>
            )}
          </div>
          {showLog && <pre className="search-log">{log}</pre>}
          {results.map((job) => (
            <JobCard key={job.url} job={job} />
          ))}
        </div>
      )}
    </div>
  );
}
