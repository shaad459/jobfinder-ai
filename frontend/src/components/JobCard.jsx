import { useState } from "react";
import { api } from "../api";

const TIER_CLASS = { Strong: "badge-strong", Good: "badge-good", Weak: "badge-weak" };

function ScorePill({ score, expanded, onClick }) {
  const tierClass = TIER_CLASS[score.match_tier] || "badge-weak";
  return (
    <button className={`score-pill ${tierClass} ${expanded ? "score-pill-expanded" : ""}`} onClick={onClick}>
      <span className="score-pill-label">{score.label || `Resume #${score.profile_id}`}</span>
      <span className="score-pill-value">
        {score.match_score != null ? `${score.match_score}%` : "—"} {score.match_tier || ""}
      </span>
    </button>
  );
}

// The whole point of the resume library: one job, several scores side by side - "89% as
// Product Owner, 83% as Business Analyst" - instead of forcing a separate search per resume.
// Click a score pill to expand that specific resume's matching points/gaps/reasoning for this
// job, since those differ per resume even though the job itself is the same.
export default function JobCard({ job }) {
  const [expandedProfileId, setExpandedProfileId] = useState(job.scores[0]?.profile_id ?? null);
  const expanded = job.scores.find((s) => s.profile_id === expandedProfileId);

  async function handleMarkOpened(profileId) {
    window.open(job.url, "_blank", "noopener,noreferrer");
    try {
      await api.markOpened(profileId, job.url);
    } catch {
      // non-critical - opening the link already succeeded
    }
  }

  return (
    <div className="job-card">
      <div className="job-card-top">
        <div>
          <div className="job-card-title">{job.title}</div>
          <div className="job-card-meta">
            {job.company} {job.location && `· ${job.location}`} {job.posted_date && `· ${job.posted_date}`}
            {job.source && <span className="pill pill-muted">{job.source}</span>}
          </div>
        </div>
        <button className="btn-primary" onClick={() => handleMarkOpened(expanded?.profile_id ?? job.scores[0].profile_id)}>
          Open posting ↗
        </button>
      </div>

      <div className="score-pill-row">
        {job.scores
          .slice()
          .sort((a, b) => (b.match_score || 0) - (a.match_score || 0))
          .map((s) => (
            <ScorePill
              key={s.profile_id}
              score={s}
              expanded={s.profile_id === expandedProfileId}
              onClick={() => setExpandedProfileId(s.profile_id === expandedProfileId ? null : s.profile_id)}
            />
          ))}
      </div>

      {expanded && (
        <div className="job-card-detail">
          <div className="job-card-detail-heading">
            As <strong>{expanded.label}</strong>: {expanded.match_reasoning || "No summary available."}
          </div>
          <div className="job-card-detail-columns">
            <div>
              <div className="detail-col-heading">Matching points</div>
              {expanded.match_points?.length ? (
                <ul>
                  {expanded.match_points.map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">None recorded.</p>
              )}
            </div>
            <div>
              <div className="detail-col-heading">Gaps</div>
              {expanded.match_gaps?.length ? (
                <ul>
                  {expanded.match_gaps.map((g, i) => (
                    <li key={i}>{g}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">None recorded.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
