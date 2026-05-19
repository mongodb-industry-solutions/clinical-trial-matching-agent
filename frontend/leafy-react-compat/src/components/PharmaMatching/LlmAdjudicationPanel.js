import React from "react";

const formatStatus = (value) => {
  return String(value || "UNKNOWN").toUpperCase();
};

export default function LlmAdjudicationPanel({
  queue,
  loading,
  adjudication,
  onRunChecks,
  maxChecks = 10,
  deterministicUnknownCount = 0,
  deterministicUnknownMissing = [],
}) {
  const pending = Array.isArray(queue) ? queue : [];
  const executionRows = Array.isArray(adjudication?.executionRows) ? adjudication.executionRows : [];
  const hasExecution = executionRows.length > 0;
  const jobStatus = String(adjudication?.jobStatus || "").toLowerCase();
  const shouldRender = loading
    || pending.length > 0
    || hasExecution
    || ["queued", "running", "completed", "failed"].includes(jobStatus);

  if (!shouldRender) return null;

  return (
    <div className="pharma-llm-panel">
      <div className="pharma-llm-panel-head">
        <strong>Narrative review queue</strong>
        {pending.length > 0 || loading ? (
          <button
            type="button"
            className="pharma-btn secondary"
            onClick={onRunChecks}
            disabled={loading || pending.length === 0}
          >
            {loading ? `Running up to ${maxChecks} checks...` : `Run up to ${maxChecks} checks`}
          </button>
        ) : null}
      </div>

      <p className="pharma-muted">
        Only criteria that still need narrative interpretation appear here.
      </p>

      {pending.length > 0 ? (
        <ul className="pharma-list">
          {pending.slice(0, maxChecks).map((row, idx) => (
            <li key={`${row?.ruleId || idx}`}>
              <strong>{row?.ruleId || `Rule ${idx + 1}`}</strong>
              <span>{row?.text || "-"}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {adjudication ? (
        <div className="pharma-llm-summary">
          <div className="pharma-kv-grid">
            <div><span>Job Status</span><strong>{jobStatus || "-"}</strong></div>
            <div><span>Requested</span><strong>{adjudication.requestedChecks ?? 0}</strong></div>
            <div><span>Processed</span><strong>{adjudication.processedChecks ?? 0}</strong></div>
            <div><span>Parallelism</span><strong>{adjudication.parallelism ?? 0}</strong></div>
            <div><span>From Cache</span><strong>{adjudication.usedCache ? "Yes" : "No"}</strong></div>
          </div>
          {adjudication?.error ? (
            <p className="pharma-candidate-pipeline-fallback">
              Job error: {String(adjudication.error)}
            </p>
          ) : null}
          {hasExecution ? (
            <ul className="pharma-list">
              {executionRows.slice(0, maxChecks).map((item, idx) => (
                <li key={`${item?.ruleId || idx}-exec`}>
                  <strong>{item?.ruleId || `Rule ${idx + 1}`}</strong>
                  <span>{formatStatus(item?.status)} in {item?.durationMs ?? 0}ms ({item?.source || "llm"})</span>
                </li>
              ))}
            </ul>
          ) : null}
          {!hasExecution && deterministicUnknownCount > 0 && Array.isArray(deterministicUnknownMissing) && deterministicUnknownMissing.length > 0 ? (
            <p className="pharma-muted" style={{ marginTop: 10 }}>
              Deterministic gaps still open: {deterministicUnknownMissing.join(", ")}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
