import React from "react";

const factValue = (facts, key, fallback = "-") => {
  const value = (((facts || {})[key] || {}).value);
  if (value == null) return fallback;
  if (Array.isArray(value)) return value.length ? value.join(", ") : fallback;
  if (typeof value === "object") return Object.keys(value).length ? JSON.stringify(value) : fallback;
  return String(value);
};

export default function EvidencePackSummary({ evidencePack }) {
  if (!evidencePack || typeof evidencePack !== "object") return null;

  const facts = evidencePack.facts || {};
  const missingFields = Array.isArray(evidencePack.missing_fields) ? evidencePack.missing_fields : [];
  const provenance = evidencePack.source_provenance || {};

  return (
    <div className="pharma-evidence-pack">
      <div className="pharma-kv-grid">
        <div><span>Synthea Ref</span><strong>{provenance.syntheaPatientRef || "-"}</strong></div>
        <div><span>Leafy Appointment</span><strong>{provenance.leafyAppointmentId || "-"}</strong></div>
        <div><span>Age</span><strong>{factValue(facts, "age")}</strong></div>
        <div><span>Sex</span><strong>{factValue(facts, "sex")}</strong></div>
        <div><span>BI-RADS</span><strong>{factValue(facts, "biradsValues")}</strong></div>
        <div><span>Latest BMI</span><strong>{factValue(facts, "latestBmi")}</strong></div>
      </div>
      {missingFields.length > 0 ? (
        <div className="pharma-evidence-missing">
          <strong>Missing fields:</strong> {missingFields.join(", ")}
        </div>
      ) : null}
    </div>
  );
}
