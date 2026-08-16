import { useEffect, useState } from "react";
import { api } from "./api";
import ResumeLibrary from "./components/ResumeLibrary";
import SearchPanel from "./components/SearchPanel";

export default function App() {
  const [resumes, setResumes] = useState([]);
  const [companies, setCompanies] = useState({});
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [backendOk, setBackendOk] = useState(null);
  const [cacheStatus, setCacheStatus] = useState(null);

  async function refreshResumes() {
    const data = await api.listResumes(true);
    setResumes(data);
  }

  async function refreshCompanies() {
    const data = await api.listCompanies();
    setCompanies(data);
  }

  useEffect(() => {
    api
      .health()
      .then(() => setBackendOk(true))
      .catch(() => setBackendOk(false));
    refreshResumes();
    refreshCompanies();
    // Pull the shared ~12h job cache once on load, same as streamlit_app.py does at startup -
    // silent on failure, since every search falls back to a live fetch regardless.
    api
      .syncJobCache()
      .then((r) => setCacheStatus(r))
      .catch(() => {});
  }, []);

  function toggleSelect(id, checked) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  if (backendOk === false) {
    return (
      <div className="app-shell">
        <div className="panel error-panel">
          <h2>Can't reach the JobScout AI backend</h2>
          <p>
            Start it from the <code>app/</code> folder with:
          </p>
          <pre className="search-log">uvicorn api_server:app --reload --port 8000</pre>
          <p className="muted">Then reload this page.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>JobScout AI</h1>
        {cacheStatus?.message && <span className="muted small">{cacheStatus.message}</span>}
      </header>

      <ResumeLibrary
        resumes={resumes}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onRefresh={refreshResumes}
      />

      <SearchPanel
        resumes={resumes}
        selectedIds={selectedIds}
        companies={companies}
        onRefreshCompanies={refreshCompanies}
      />
    </div>
  );
}
