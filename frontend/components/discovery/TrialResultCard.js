"use client";

import { renderHighlightedText } from "./searchUtils";

const PHASE_COLORS = {
  PHASE1: "mdb-badge-gray",
  PHASE2: "mdb-badge-blue",
  PHASE3: "mdb-badge-green",
  PHASE4: "mdb-badge-green",
};

const STATUS_COLORS = {
  RECRUITING: "mdb-badge-green",
  ACTIVE_NOT_RECRUITING: "mdb-badge-amber",
  COMPLETED: "mdb-badge-gray",
  NOT_YET_RECRUITING: "mdb-badge-blue",
  TERMINATED: "mdb-badge-red",
  SUSPENDED: "mdb-badge-red",
  WITHDRAWN: "mdb-badge-gray",
};

function normalizePhase(phase) {
  return String(phase || "")
    .toUpperCase()
    .replace(/\s+/g, "")
    .replace("PHASE", "Phase ");
}

function normalizeStatus(status) {
  return String(status || "")
    .split("_")
    .map((w) => w[0]?.toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

export default function TrialResultCard({ trial, onSelect, onIngest, query = "" }) {
  const {
    nct_id,
    title,
    brief_title,
    phase,
    status,
    sponsor,
    conditions = [],
    source_last_updated,
    ingested,
    readiness,
  } = trial;

  const displayTitle = brief_title || title || "Untitled Trial";
  const phaseKey = String(phase || "").toUpperCase().replace(/\s+/g, "");
  const statusKey = String(status || "").toUpperCase().replace(/\s+/g, "_");

  return (
    <div className="mdb-trial-card" onClick={() => onSelect?.(trial)}>
      <div className="mdb-trial-card-header">
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <span className="mdb-mono" style={{ fontSize: 11, color: "var(--mdb-forest)", fontWeight: 600 }}>
              {nct_id}
            </span>
            {phase && (
              <span className={`mdb-badge ${PHASE_COLORS[phaseKey] || "mdb-badge-gray"}`}>
                {normalizePhase(phase)}
              </span>
            )}
            {status && (
              <span className={`mdb-badge ${STATUS_COLORS[statusKey] || "mdb-badge-gray"}`}>
                <span className="mdb-badge-dot" />
                {normalizeStatus(status)}
              </span>
            )}
            {ingested && (
              <span className="mdb-badge mdb-badge-green" title="Ingested into knowledge base">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                Ingested
              </span>
            )}
          </div>
          <p className="mdb-trial-card-title">{renderHighlightedText(displayTitle, query)}</p>
        </div>
        <button
          className="mdb-btn mdb-btn-secondary mdb-btn-sm"
          onClick={(e) => { e.stopPropagation(); onIngest?.(trial); }}
          title="Ingest trial into knowledge base"
          style={{ flexShrink: 0, marginTop: 2 }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12l7 7 7-7"/>
          </svg>
          Ingest
        </button>
      </div>

      <div className="mdb-trial-card-meta">
        {sponsor && (
          <span className="mdb-trial-card-meta-item">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
            </svg>
            {renderHighlightedText(sponsor, query)}
          </span>
        )}
        {source_last_updated && (
          <span className="mdb-trial-card-meta-item">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
            Updated {source_last_updated}
          </span>
        )}
      </div>

      {conditions.length > 0 && (
        <div className="mdb-trial-card-footer">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--mdb-slate-30)" strokeWidth="2" style={{ flexShrink: 0 }}>
            <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
          </svg>
          <span className="mdb-trial-card-conditions">
            {renderHighlightedText(conditions.slice(0, 4).join("  ·  "), query)}
            {conditions.length > 4 && `  +${conditions.length - 4} more`}
          </span>
        </div>
      )}
    </div>
  );
}
