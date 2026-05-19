import React from "react";

const STATUS_MAP = {
  met: "met",
  pass: "met",
  candidate: "met",
  not_met: "not-met",
  "not-met": "not-met",
  fail: "not-met",
  excluded: "not-met",
  unknown: "unknown",
  needs_more_data: "unknown",
  "needs-data": "unknown",
  manual_review_required: "manual",
  manual: "manual",
  blocked_by_linkage_conflict: "conflict",
  conflict: "conflict",
  fillable_by_questionnaire: "fillable",
  fillable: "fillable",
  source: "source",
  canonical: "canonical",
  not_patient_rule: "neutral",
};

function humanize(str) {
  if (!str) return "";
  return str
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function StatusChip({ status, label, size, children }) {
  const variant = STATUS_MAP[status] || "neutral";
  const sizeClass = size === "sm" ? "pds-chip--sm" : "";

  return (
    <span className={`pds-chip pds-chip--${variant} ${sizeClass}`.trim()}>
      {label || children || humanize(status)}
    </span>
  );
}

export default StatusChip;
