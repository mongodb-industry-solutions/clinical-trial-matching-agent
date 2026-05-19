import React from "react";

const asNumber = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
};

const truncate = (value, max = 160) => {
  const text = String(value || "").trim();
  if (!text || text.length <= max) return text;
  return `${text.slice(0, max - 1)}...`;
};

export default function EligibilityDecisionSummary({
  assessment,
  decisionSummary,
  missingCriteria,
  compact = false,
}) {
  const summary = decisionSummary || {};
  const totalRules = asNumber(summary.totalRules);
  const passRules = asNumber(summary.passRules);
  const failRules = asNumber(summary.failRules);
  const unknownRules = asNumber(summary.unknownRules);
  const reason = summary.reason || "";
  const missing = Array.isArray(summary.missingDataFields) && summary.missingDataFields.length > 0
    ? summary.missingDataFields
    : (Array.isArray(missingCriteria) ? missingCriteria : []);
  const shouldRender = Boolean(reason || totalRules > 0 || missing.length > 0 || assessment);

  if (!shouldRender) return null;

  const renderedAssessment = (() => {
    const normalized = String(assessment || "").toLowerCase();
    if (normalized === "likely_eligible") return "likely eligible";
    if (normalized === "likely_ineligible") return "likely ineligible";
    if (normalized === "needs_review" || normalized === "unknown") return "needs review";
    return normalized ? normalized.replaceAll("_", " ") : "";
  })();

  return (
    <div className={`pharma-decision-summary ${compact ? "compact" : ""}`}>
      <div className="pharma-decision-summary-head">
        <strong>{compact ? "Decision" : "Decision summary"}</strong>
        {renderedAssessment ? <span>{renderedAssessment}</span> : null}
      </div>
      {reason ? <p>{compact ? truncate(reason, 120) : reason}</p> : null}
      <div className="pharma-decision-metrics">
        <span>Rules: {totalRules}</span>
        <span>PASS: {passRules}</span>
        <span>FAIL: {failRules}</span>
        <span>UNKNOWN: {unknownRules}</span>
      </div>
      {unknownRules > 0 ? (
        <div className="pharma-decision-note">
          UNKNOWN means missing evidence, not an active background task.
        </div>
      ) : null}
      {missing.length > 0 ? (
        <div className="pharma-decision-missing">
          Missing data: {missing.slice(0, compact ? 4 : 8).join(", ")}
        </div>
      ) : null}
    </div>
  );
}
