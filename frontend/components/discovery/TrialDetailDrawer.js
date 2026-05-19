"use client";

import { useState, useEffect } from "react";
import TrialMap from "./TrialMap";

const PHASE_COLORS = {
  PHASE1: "mdb-badge-gray", PHASE2: "mdb-badge-blue",
  PHASE3: "mdb-badge-green", PHASE4: "mdb-badge-green",
};
const STATUS_COLORS = {
  RECRUITING: "mdb-badge-green", ACTIVE_NOT_RECRUITING: "mdb-badge-amber",
  COMPLETED: "mdb-badge-gray", NOT_YET_RECRUITING: "mdb-badge-blue",
  TERMINATED: "mdb-badge-red", SUSPENDED: "mdb-badge-red", WITHDRAWN: "mdb-badge-gray",
};

function fmt(str) {
  if (!str) return null;
  return String(str).split("_").map((w) => w[0]?.toUpperCase() + w.slice(1).toLowerCase()).join(" ");
}

function MetaRow({ label, children }) {
  if (!children) return null;
  return (
    <div style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: "1px solid var(--mdb-border)", alignItems: "flex-start" }}>
      <span style={{ width: 130, flexShrink: 0, fontSize: 12, fontWeight: 700, color: "var(--mdb-slate-30)", textTransform: "uppercase", letterSpacing: "0.04em", paddingTop: 1 }}>
        {label}
      </span>
      <span style={{ fontSize: 13, color: "var(--mdb-slate)", flex: 1, lineHeight: 1.5 }}>{children}</span>
    </div>
  );
}


export default function TrialDetailDrawer({ trial, onClose, onIngest }) {
  const [ingestedDetail, setIngestedDetail] = useState(null);
  const [activeTab, setActiveTab] = useState("overview");

  const nctId = trial?.nct_id;
  const phaseKey = String(trial?.phase || "").toUpperCase().replace(/\s+/g, "");
  const statusKey = String(trial?.status || "").toUpperCase().replace(/\s+/g, "_");

  // Try fetching canonical detail if trial was ingested
  useEffect(() => {
    if (!trial?.ingested || !nctId) return;
    import("../../lib/api").then(({ api }) => {
      api.listTrials({ search: nctId, limit: 1 })
        .then((data) => {
          const found = (data?.trials || data?.results || []).find((t) => t.nct_id === nctId);
          if (found) setIngestedDetail(found);
        })
        .catch(() => {});
    });
  }, [trial?.ingested, nctId]);

  // Close on Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: "rgba(0,30,43,0.35)", zIndex: 100, backdropFilter: "blur(1px)" }}
      />

      {/* Drawer */}
      <div style={{
        position: "fixed",
        top: 0,
        right: 0,
        bottom: 0,
        width: 580,
        background: "var(--mdb-white)",
        zIndex: 101,
        boxShadow: "-8px 0 32px rgba(0,0,0,0.14)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        animation: "slideInRight 0.22s ease-out",
      }}>
        {/* Header */}
        <div style={{ padding: "18px 22px 14px", borderBottom: "1px solid var(--mdb-border)", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                <span className="mdb-mono" style={{ fontSize: 12, color: "var(--mdb-forest)", fontWeight: 700 }}>{nctId}</span>
                {trial?.phase && (
                  <span className={`mdb-badge ${PHASE_COLORS[phaseKey] || "mdb-badge-gray"}`}>
                    {fmt(trial.phase)}
                  </span>
                )}
                {trial?.status && (
                  <span className={`mdb-badge ${STATUS_COLORS[statusKey] || "mdb-badge-gray"}`}>
                    <span className="mdb-badge-dot" />{fmt(trial.status)}
                  </span>
                )}
                {trial?.ingested && (
                  <span className="mdb-badge mdb-badge-green" style={{ fontSize: 10 }}>
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                    In Knowledge Base
                  </span>
                )}
              </div>
              <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "var(--mdb-slate)", lineHeight: 1.4 }}>
                {trial?.brief_title || trial?.title || "Untitled Trial"}
              </h2>
            </div>
            <button
              className="mdb-btn mdb-btn-ghost mdb-btn-icon"
              onClick={onClose}
              style={{ flexShrink: 0 }}
              aria-label="Close"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          {/* Tab bar */}
          <div style={{ display: "flex", gap: 2, marginTop: 12 }}>
            {[["overview", "Overview"], ["map", "Locations"]].map(([val, label]) => (
              <button
                key={val}
                onClick={() => setActiveTab(val)}
                style={{
                  background: "none", border: "none", padding: "6px 12px", fontSize: 13, fontWeight: 600,
                  cursor: "pointer", color: activeTab === val ? "var(--mdb-forest)" : "var(--mdb-slate-30)",
                  borderBottom: activeTab === val ? "2px solid var(--mdb-forest)" : "2px solid transparent",
                  transition: "color 0.1s",
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 22px" }}>

          {/* OVERVIEW TAB */}
          {activeTab === "overview" && (
            <>
              {/* Key stats row */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginBottom: 18 }}>
                {[
                  { label: "Sponsor", value: trial?.sponsor },
                  { label: "Phase", value: fmt(trial?.phase) },
                  { label: "Updated", value: trial?.source_last_updated },
                ].map(({ label, value }) => value ? (
                  <div key={label} style={{ background: "var(--mdb-bg-subtle)", borderRadius: "var(--mdb-radius)", padding: "10px 12px" }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: "var(--mdb-slate-30)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>{label}</div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--mdb-slate)" }}>{value}</div>
                  </div>
                ) : null)}
              </div>

              {/* Conditions */}
              {(trial?.conditions || []).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "var(--mdb-slate-30)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8 }}>Conditions</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {trial.conditions.map((c) => (
                      <span key={c} className="mdb-badge mdb-badge-gray">{c}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* Metadata table */}
              <div style={{ marginBottom: 18 }}>
                <MetaRow label="NCT ID">
                  <a href={`https://clinicaltrials.gov/study/${nctId}`} target="_blank" rel="noopener noreferrer"
                    style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                    {nctId}
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                      <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
                    </svg>
                  </a>
                </MetaRow>
                <MetaRow label="Sponsor">{trial?.sponsor}</MetaRow>
                <MetaRow label="Phase">{fmt(trial?.phase)}</MetaRow>
                <MetaRow label="Status">{fmt(trial?.status)}</MetaRow>
                <MetaRow label="Last Updated">{trial?.source_last_updated}</MetaRow>
                {ingestedDetail && (
                  <>
                    <MetaRow label="Readiness">{ingestedDetail?.readiness || ingestedDetail?.runtime_readiness}</MetaRow>
                    <MetaRow label="Criteria">{ingestedDetail?.criteria_count != null ? `${ingestedDetail.criteria_count} compiled` : null}</MetaRow>
                  </>
                )}
              </div>

              {/* CTA row */}
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <a
                  href={`https://clinicaltrials.gov/study/${nctId}`}
                  target="_blank" rel="noopener noreferrer"
                  className="mdb-btn mdb-btn-secondary mdb-btn-sm"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                    <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
                  </svg>
                  ClinicalTrials.gov
                </a>
                {!trial?.ingested && (
                  <button
                    className="mdb-btn mdb-btn-primary mdb-btn-sm"
                    onClick={() => onIngest?.(trial)}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M12 5v14M5 12l7 7 7-7"/>
                    </svg>
                    Ingest into Atlas
                  </button>
                )}
                {trial?.ingested && (
                  <a href={`/match?trial=${nctId}`} className="mdb-btn mdb-btn-primary mdb-btn-sm">
                    Run Match Copilot →
                  </a>
                )}
              </div>
            </>
          )}

          {/* MAP TAB */}
          {activeTab === "map" && (
            <>
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--mdb-slate)", marginBottom: 4 }}>Trial Site Locations</div>
                <div style={{ fontSize: 12.5, color: "var(--mdb-slate-60)" }}>
                  Facility locations fetched from ClinicalTrials.gov. Click a marker for site details.
                </div>
              </div>
              <TrialMap nctId={nctId} height={380} />
              <div style={{ marginTop: 14, padding: "10px 14px", background: "var(--mdb-bg-subtle)", borderRadius: "var(--mdb-radius)", fontSize: 12, color: "var(--mdb-slate-60)", display: "flex", gap: 6 }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, marginTop: 1 }}>
                  <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
                Map tiles from OpenStreetMap. Location data sourced from ClinicalTrials.gov API v2.
              </div>
            </>
          )}

        </div>
      </div>

      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(40px); opacity: 0; }
          to   { transform: translateX(0);   opacity: 1; }
        }
      `}</style>
    </>
  );
}
