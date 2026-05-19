import React from "react";

const pct = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(100, numeric));
};

export default function CoverageMeter({ coverage, emptyLabel = "No patient-specific rule coverage yet. Run matching for this trial to generate evidence-based evaluation." }) {
  if (!coverage || typeof coverage !== "object") return null;
  const hasRules = coverage.hasRules !== false;
  const totalRules = Number(coverage.totalRules || 0);
  if (!hasRules || !Number.isFinite(totalRules) || totalRules <= 0) {
    return (
      <div className="pharma-coverage-empty">
        {emptyLabel}
      </div>
    );
  }

  const deterministicPct = pct(coverage.deterministicCoveragePct);
  const llmPct = pct(coverage.llmCoveragePct);
  const unknownPct = pct(coverage.unknownPct);
  const evaluatedRules = Number(coverage.evaluatedRules || 0);
  const unknownRules = Number(coverage.unknownRules || 0);

  return (
    <div className="pharma-coverage-meter">
      <div className="pharma-coverage-bar">
        <span className="deterministic" style={{ width: `${deterministicPct}%` }} />
        <span className="llm" style={{ width: `${llmPct}%` }} />
        <span className="unknown" style={{ width: `${unknownPct}%` }} />
      </div>
      <div className="pharma-coverage-legend" style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px", marginTop: 8, fontSize: 12 }}>
        <span>Rules: <strong>{totalRules}</strong></span>
        <span>Evaluated: <strong>{evaluatedRules}</strong></span>
        <span>Deterministic: <strong>{deterministicPct.toFixed(1)}%</strong></span>
        {llmPct > 0 ? <span>Narrative review: <strong>{llmPct.toFixed(1)}%</strong></span> : null}
        <span>Unknown: <strong>{unknownPct.toFixed(1)}%</strong> ({unknownRules})</span>
      </div>
    </div>
  );
}
