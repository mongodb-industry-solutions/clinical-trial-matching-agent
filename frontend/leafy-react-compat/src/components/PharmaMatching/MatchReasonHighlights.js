import React from "react";

const truncate = (value, max = 92) => {
  const text = String(value || "").trim();
  if (!text) return "-";
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}...`;
};

const labelize = (value) => String(value || "").replaceAll("_", " ").trim() || "-";

const flattenSimpleValue = (value) => {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) return value.slice(0, 2).join(", ");
  if (typeof value === "object") {
    const entries = Object.entries(value)
      .filter(([, item]) => item != null && item !== "" && !(Array.isArray(item) && item.length === 0))
      .slice(0, 2)
      .map(([key, item]) => `${labelize(key)} ${Array.isArray(item) ? item.slice(0, 2).join(", ") : String(item)}`);
    return entries.join(" · ");
  }
  return String(value);
};

const explainRow = (row) => {
  const status = String(row?.status || "UNKNOWN").toUpperCase();
  const text = truncate(row?.text || row?.criterion || row?.ruleId || "criterion");
  const patientValue = flattenSimpleValue(row?.patientValue);
  const trialValue = flattenSimpleValue(row?.trialValue);
  const missing = Array.isArray(row?.missingData) ? row.missingData : [];
  const manualReason = String(row?.manualReviewReason || "").trim();

  if (status === "PASS") {
    return {
      text,
      reason: patientValue ? `Found: ${truncate(patientValue, 56)}` : (trialValue ? `Matched required condition` : "Supported by patient evidence"),
    };
  }
  if (status === "FAIL") {
    return {
      text,
      reason: trialValue ? `Did not satisfy: ${truncate(trialValue, 56)}` : "Blocked by patient evidence",
    };
  }
  return {
    text,
    reason: missing.length > 0
      ? `Missing: ${truncate(missing.slice(0, 2).join(", "), 56)}`
      : (manualReason || "Needs narrative or manual review"),
  };
};

const buildBucket = (rules, targetStatus, maxItems) => (
  (Array.isArray(rules) ? rules : [])
    .filter((row) => String(row?.status || "").toUpperCase() === targetStatus)
    .slice(0, maxItems)
    .map(explainRow)
);

export default function MatchReasonHighlights({ rules, maxItemsPerBucket = 2, title = "Why this decision" }) {
  const matched = buildBucket(rules, "PASS", maxItemsPerBucket);
  const blocked = buildBucket(rules, "FAIL", maxItemsPerBucket);
  const unknown = buildBucket(rules, "UNKNOWN", maxItemsPerBucket);

  if (matched.length === 0 && blocked.length === 0 && unknown.length === 0) return null;

  const groups = [
    { key: "matched", title: "Matched because", tone: "pass", rows: matched },
    { key: "blocked", title: "Blocked by", tone: "fail", rows: blocked },
    { key: "unknown", title: "Still unknown", tone: "review", rows: unknown },
  ].filter((group) => group.rows.length > 0);

  return (
    <div className="pharma-match-reasons">
      <div className="pharma-evidence-group-label">{title}</div>
      <div className="pharma-match-reasons-grid">
        {groups.map((group) => (
          <div key={group.key} className="pharma-match-reason-card">
            <span className={`pharma-rule-assertion-chip ${group.tone}`}>{group.title}</span>
            <div className="pharma-match-reason-list">
              {group.rows.map((item, index) => (
                <div key={`${group.key}-${index}`} className="pharma-match-reason-item">
                  <strong>{item.text}</strong>
                  <span>{item.reason}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
