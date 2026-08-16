import { useState } from "react";
import { api } from "../api";

export default function CompanyManager({ companies, onRefresh }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleAdd(e) {
    e.preventDefault();
    if (!name.trim() || !url.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.addCompany(name.trim(), url.trim());
      setName("");
      setUrl("");
      onRefresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRemove(companyName) {
    await api.removeCompany(companyName);
    onRefresh();
  }

  return (
    <details className="collapsible">
      <summary>Manage companies ({Object.keys(companies).length})</summary>
      <ul className="company-list">
        {Object.keys(companies)
          .sort()
          .map((n) => (
            <li key={n}>
              <span>{n}</span>
              <button className="btn-ghost btn-small" onClick={() => handleRemove(n)}>
                Remove
              </button>
            </li>
          ))}
      </ul>
      <form className="inline-form" onSubmit={handleAdd}>
        <input placeholder="Display name (e.g. Tesla)" value={name} onChange={(e) => setName(e.target.value)} />
        <input
          placeholder="Workday careers URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button className="btn-secondary" type="submit" disabled={submitting}>
          {submitting ? "Adding…" : "Add company"}
        </button>
      </form>
      {error && <div className="error-text">{error}</div>}
    </details>
  );
}
