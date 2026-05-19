import React from "react";

const SEGMENTS = [
  { key: "met", label: "Met", className: "pds-status-bar__segment--met" },
  { key: "not_met", label: "Not Met", className: "pds-status-bar__segment--not-met" },
  { key: "unknown", label: "Unknown", className: "pds-status-bar__segment--unknown" },
  { key: "manual", label: "Manual Review", className: "pds-status-bar__segment--manual" },
  { key: "fillable", label: "Fillable", className: "pds-status-bar__segment--fillable" },
  { key: "conflict", label: "Conflict", className: "pds-status-bar__segment--conflict" },
];

function CriterionStatusBar({ counts, showLegend = true }) {
  if (!counts) return null;

  const total = SEGMENTS.reduce((sum, seg) => sum + (counts[seg.key] || 0), 0);
  if (total === 0) return null;

  return (
    <div className="pds-status-bar-container">
      <div className="pds-status-bar">
        {SEGMENTS.map((seg) => {
          const count = counts[seg.key] || 0;
          if (count === 0) return null;
          const pct = (count / total) * 100;
          return (
            <div
              key={seg.key}
              className={`pds-status-bar__segment ${seg.className}`}
              style={{ width: `${pct}%` }}
              title={`${seg.label}: ${count} (${Math.round(pct)}%)`}
            />
          );
        })}
      </div>

      {showLegend && (
        <div className="pds-status-bar__legend">
          {SEGMENTS.map((seg) => {
            const count = counts[seg.key] || 0;
            if (count === 0) return null;
            return (
              <span key={seg.key} className="pds-status-bar__legend-item">
                <span className={`pds-status-bar__legend-dot ${seg.className}`} />
                <span className="pds-status-bar__legend-label">
                  {seg.label} ({count})
                </span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default CriterionStatusBar;
