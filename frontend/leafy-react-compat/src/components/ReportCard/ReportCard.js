import React from "react";

function formatDate(value) {
  if (!value) {
    return "Unknown date";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString();
}

function renderSeries(series = []) {
  if (!Array.isArray(series) || series.length === 0) {
    return <div style={{ color: "#64748b", fontSize: 13 }}>No imaging series attached.</div>;
  }

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {series.slice(0, 8).map((item, index) => {
        const meta = item?.imageMetadata || {};
        const laterality = meta?.ImageLaterality || item?.laterality || "-";
        const view = meta?.ViewPosition || item?.viewPosition || "-";
        const birads = meta?.biRads?.BIRAD || meta?.biRads || item?.birads || "-";
        return (
          <div
            key={`${laterality}-${view}-${index}`}
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
              border: "1px solid #e2e8f0",
              borderRadius: 10,
              padding: "10px 12px",
              background: "#f8fafc",
              fontSize: 13,
            }}
          >
            <span>{`${laterality} ${view}`.trim()}</span>
            <strong>{`BI-RADS ${birads}`}</strong>
          </div>
        );
      })}
    </div>
  );
}

export default function ReportCard({
  patientFullName,
  patientID,
  appointmentID,
  conclusions,
  clinicalNotes,
  biopsyResults,
  biopsyArtifact,
  series,
  date,
}) {
  return (
    <div
      style={{
        display: "grid",
        gap: 14,
        border: "1px solid #e2e8f0",
        borderRadius: 16,
        padding: 16,
        background: "#ffffff",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: "0.08em", color: "#0f766e" }}>
            Screening report
          </div>
          <div style={{ fontSize: 18, fontWeight: 700, color: "#0f172a" }}>
            {patientFullName || "Unknown patient"}
          </div>
        </div>
        <div style={{ fontSize: 12, color: "#475569", textAlign: "right" }}>
          <div>{formatDate(date)}</div>
          {appointmentID ? <div>{`Appointment ${appointmentID}`}</div> : null}
          {patientID ? <div>{`Patient ${patientID}`}</div> : null}
        </div>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        <div>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Clinical notes</div>
          <div style={{ color: "#334155", fontSize: 14, lineHeight: 1.5 }}>
            {clinicalNotes || "No clinical notes available."}
          </div>
        </div>
        <div>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Conclusions</div>
          <div style={{ color: "#334155", fontSize: 14, lineHeight: 1.5 }}>
            {conclusions || "No conclusions available."}
          </div>
        </div>
      </div>

      {(biopsyResults || biopsyArtifact) && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Biopsy context</div>
          <pre
            style={{
              margin: 0,
              overflowX: "auto",
              padding: 12,
              borderRadius: 12,
              background: "#0f172a",
              color: "#e2e8f0",
              fontSize: 12,
            }}
          >
            {JSON.stringify(biopsyResults || biopsyArtifact, null, 2)}
          </pre>
        </div>
      )}

      <div>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Imaging series</div>
        {renderSeries(series)}
      </div>
    </div>
  );
}
