import React, { useState } from "react";
import StatusChip from "./StatusChip";

const ICONS = {
  met: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="4,10 8,14 16,6" />
    </svg>
  ),
  not_met: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
      <line x1="5" y1="5" x2="15" y2="15" />
      <line x1="15" y1="5" x2="5" y2="15" />
    </svg>
  ),
  unknown: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
      <text x="10" y="15" textAnchor="middle" fontSize="14" fill="currentColor" stroke="none">?</text>
    </svg>
  ),
  manual: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="10" cy="8" r="3" />
      <path d="M4 18 C4 14 16 14 16 18" />
      <line x1="17" y1="5" x2="17" y2="11" />
      <line x1="14" y1="8" x2="20" y2="8" />
    </svg>
  ),
  fillable: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="4" y="2" width="12" height="16" rx="1" />
      <line x1="7" y1="6" x2="13" y2="6" />
      <line x1="7" y1="9" x2="13" y2="9" />
      <line x1="7" y1="12" x2="10" y2="12" />
    </svg>
  ),
  conflict: (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M10 2 L18 17 H2 Z" />
      <line x1="10" y1="8" x2="10" y2="12" />
      <circle cx="10" cy="14.5" r="0.5" fill="currentColor" />
    </svg>
  ),
};

function CriterionCard({
  status,
  title,
  reason,
  familyId,
  patientValue,
  trialValue,
  evidenceRefs,
  evaluator,
  rawText,
  children,
}) {
  const [expanded, setExpanded] = useState(false);

  const icon = ICONS[status] || ICONS.unknown;

  return (
    <div className={`pds-criterion-card pds-criterion-card--${status || "unknown"}`}>
      <div
        className="pds-criterion-card__header"
        onClick={() => setExpanded((prev) => !prev)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded((prev) => !prev);
          }
        }}
      >
        <span className="pds-criterion-card__icon">{icon}</span>
        <div className="pds-criterion-card__body">
          <div className="pds-criterion-card__title">{title}</div>
          {reason && <div className="pds-criterion-card__reason">{reason}</div>}
        </div>
        <span className={`pds-criterion-card__chevron ${expanded ? "pds-criterion-card__chevron--open" : ""}`}>
          <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="6,8 10,12 14,8" />
          </svg>
        </span>
      </div>

      {expanded && (
        <div className="pds-criterion-card__expand">
          <div className="pds-criterion-card__details">
            {familyId && (
              <div className="pds-criterion-card__detail">
                <span className="pds-criterion-card__detail-label">Family</span>
                <span className="pds-criterion-card__detail-value">{familyId}</span>
              </div>
            )}
            {evaluator && (
              <div className="pds-criterion-card__detail">
                <span className="pds-criterion-card__detail-label">Evaluator</span>
                <span className="pds-criterion-card__detail-value">{evaluator}</span>
              </div>
            )}
            {rawText && (
              <div className="pds-criterion-card__detail">
                <span className="pds-criterion-card__detail-label">Raw text</span>
                <span className="pds-criterion-card__detail-value">{rawText}</span>
              </div>
            )}
          </div>

          {(patientValue !== undefined || trialValue !== undefined) && (
            <div className="pds-criterion-card__chips">
              {patientValue !== undefined && (
                <StatusChip status="source" label={`Patient: ${patientValue}`} size="sm" />
              )}
              {trialValue !== undefined && (
                <StatusChip status="canonical" label={`Trial: ${trialValue}`} size="sm" />
              )}
            </div>
          )}

          {evidenceRefs && evidenceRefs.length > 0 && (
            <div className="pds-criterion-card__evidence">
              <span className="pds-criterion-card__detail-label">Evidence</span>
              <ul className="pds-criterion-card__evidence-list">
                {evidenceRefs.map((ref, i) => (
                  <li key={i}>{ref}</li>
                ))}
              </ul>
            </div>
          )}

          {children}
        </div>
      )}
    </div>
  );
}

export default CriterionCard;
