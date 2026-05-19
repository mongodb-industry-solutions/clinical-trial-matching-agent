import React from "react";

const humanize = (value) => String(value || "").replaceAll("_", " ").trim() || "-";

const flattenValueChips = (value, prefix = "") => {
  if (value == null || value === "") return [];
  if (Array.isArray(value)) {
    return value.slice(0, 6).map((item) => `${prefix}${String(item)}`);
  }
  if (typeof value === "object") {
    return Object.entries(value)
      .slice(0, 6)
      .flatMap(([key, item]) => {
        if (item == null || item === "") return [];
        if (Array.isArray(item)) {
          return item.slice(0, 3).map((entry) => `${prefix}${key}:${String(entry)}`);
        }
        if (typeof item === "object") {
          return [`${prefix}${key}:${JSON.stringify(item)}`];
        }
        return [`${prefix}${key}:${String(item)}`];
      });
  }
  return [`${prefix}${String(value)}`];
};

const assertionTone = (criterionState) => {
  const normalized = String(criterionState || "").toLowerCase();
  if (["satisfied", "not_triggered"].includes(normalized)) return "pass";
  if (["not_satisfied", "triggered"].includes(normalized)) return "fail";
  return "review";
};

const provenanceTone = (source) => {
  const normalized = String(source || "").toLowerCase();
  if (normalized.includes("synthea")) return "pass";
  if (normalized.includes("synthetic")) return "review";
  if (normalized.includes("leafy")) return "neutral";
  return "neutral";
};

const summarizeValue = (value) => {
  const chips = flattenValueChips(value);
  if (chips.length > 0) return chips.slice(0, 2).join(", ");
  return "";
};

const explainRow = (row) => {
  const status = String(row?.status || "UNKNOWN").toUpperCase();
  const patient = summarizeValue(row?.patientValue);
  const trial = summarizeValue(row?.trialValue);
  const missing = Array.isArray(row?.missingData) ? row.missingData : [];
  const manualReason = String(row?.manualReviewReason || "").trim();
  if (status === "PASS") {
    if (patient && trial) return `Patient evidence matches the trial requirement.`;
    if (patient) return `Patient evidence was found for this requirement.`;
    return "This criterion is supported by the available evidence.";
  }
  if (status === "FAIL") {
    if (patient && trial) return `Patient evidence does not satisfy this requirement.`;
    return "This criterion blocks eligibility for the current patient.";
  }
  if (missing.length > 0) {
    return `More evidence is needed to decide this criterion.`;
  }
  return manualReason || "This criterion still needs narrative or manual review.";
};

export default function RuleEvaluationTable({ rules, maxRows = 24, compact = false }) {
  if (!Array.isArray(rules) || rules.length === 0) return null;

  return (
    <div className="pharma-rule-table-wrap">
      <table className={`pharma-table pharma-rule-table ${compact ? "compact" : ""}`}>
        <thead>
          <tr>
            <th>Criterion</th>
            <th>Result</th>
            <th>Why</th>
            <th>Evidence</th>
          </tr>
        </thead>
        <tbody>
          {rules.slice(0, maxRows).map((row, index) => {
            const status = String(row?.status || "UNKNOWN").toUpperCase();
            const ruleText = String(row?.text || row?.criterion || "-");
            const renderedRuleText = compact && ruleText.length > 170 ? `${ruleText.slice(0, 170)}...` : ruleText;
            const evidencePointers = Array.isArray(row?.evidencePointers) ? row.evidencePointers : [];
            const evidenceSources = Array.isArray(row?.evidenceSources) ? row.evidenceSources : [];
            const missingData = Array.isArray(row?.missingData) ? row.missingData : [];
            const patientValueChips = flattenValueChips(row?.patientValue);
            const trialValueChips = flattenValueChips(row?.trialValue);
            return (
              <tr key={`${row?.ruleId || index}-${status}`}>
                <td>
                  <div className="pharma-rule-cell">
                    <strong>{renderedRuleText}</strong>
                    <div className="pharma-rule-assertion-row">
                      <span className="pharma-rule-assertion-chip neutral">{humanize(row?.type || "rule")}</span>
                      {row?.ruleId ? <span className="pharma-rule-assertion-chip neutral">{row.ruleId}</span> : null}
                    </div>
                    <span>{row?.requiresManualReview ? "Needs reviewer attention if still unresolved" : "Evaluated against available patient evidence"}</span>
                  </div>
                </td>
                <td>
                  <div className="pharma-rule-status-stack">
                    <span className={`pharma-rule-status ${status.toLowerCase()}`}>
                      {status}
                    </span>
                    <div className="pharma-rule-assertion-row">
                      <span className={`pharma-rule-assertion-chip ${assertionTone(row?.criterionState)}`}>
                        {humanize(row?.criterionState || "unknown")}
                      </span>
                      <span className={`pharma-rule-assertion-chip ${row?.eligibilityImpact === "blocks_eligibility" ? "fail" : row?.eligibilityImpact === "supports_eligibility" ? "pass" : "review"}`}>
                        {humanize(row?.eligibilityImpact || "unknown")}
                      </span>
                      {row?.evaluationMode ? (
                        <span className="pharma-rule-assertion-chip neutral">{humanize(row.evaluationMode)}</span>
                      ) : null}
                    </div>
                    {row?.requiresManualReview && row?.manualReviewReason ? (
                      <div className="pharma-rule-review-reason">{row.manualReviewReason}</div>
                    ) : null}
                  </div>
                </td>
                <td>
                  <div className="pharma-rule-why-cell">
                    <span className="pharma-rule-why-text">{explainRow(row)}</span>
                    {(patientValueChips.length > 0 || trialValueChips.length > 0 || missingData.length > 0) ? (
                      <div className="pharma-rule-value-cluster">
                        {patientValueChips.slice(0, 3).map((item) => (
                          <span key={`${row?.ruleId || index}-patient-${item}`} className="pharma-rule-value-chip patient">
                            {item}
                          </span>
                        ))}
                        {trialValueChips.slice(0, 3).map((item) => (
                          <span key={`${row?.ruleId || index}-trial-${item}`} className="pharma-rule-value-chip trial">
                            {item}
                          </span>
                        ))}
                        {missingData.slice(0, 3).map((item) => (
                          <span key={`${row?.ruleId || index}-missing-${item}`} className="pharma-rule-value-chip missing">
                            {item}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </td>
                <td>
                  <div className="pharma-rule-evidence-cell">
                    {evidenceSources.length > 0 ? (
                      <div className="pharma-rule-evidence-sources">
                        {evidenceSources.slice(0, 3).map((item) => (
                          <span key={`${row?.ruleId || index}-${item}`} className={`pharma-evidence-chip ${provenanceTone(item)}`}>
                            {humanize(item)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {evidencePointers.length > 0 ? (
                      <div className="pharma-rule-value-cluster">
                        {evidencePointers.slice(0, 4).map((item) => (
                          <span key={`${row?.ruleId || index}-evidence-${item}`} className="pharma-rule-value-chip evidence">
                            {item}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="pharma-muted">No explicit evidence pointer</span>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
