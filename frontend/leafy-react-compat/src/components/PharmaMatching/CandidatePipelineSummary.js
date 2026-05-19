import React from "react";

const formatFallbackReason = (reasonValue) => {
  const reason = String(reasonValue || "").trim();
  if (!reason) {
    return { headline: "", raw: "" };
  }
  const normalized = reason.toLowerCase();
  if (normalized.includes("needs to be indexed as filter")) {
    return {
      headline: "Vector search fallback: an index filter mapping is missing for this field. Structured ranking is being used.",
      raw: reason,
    };
  }
  if (normalized.includes("executor error")) {
    return {
      headline: "Vector search fallback: Atlas returned a temporary execution error. Structured retrieval continues.",
      raw: reason,
    };
  }
  if (normalized.includes("unavailable")) {
    return {
      headline: "Vector search fallback: patient-profile embeddings are not available yet. Structured deterministic retrieval is active.",
      raw: reason,
    };
  }
  if (normalized.includes("returned zero")) {
    return {
      headline: "Vector search fallback: the vector query returned zero patient profiles, so structured deterministic retrieval took over.",
      raw: reason,
    };
  }
  return {
    headline: "Vector search fallback: structured retrieval path is active.",
    raw: reason,
  };
};

export default function CandidatePipelineSummary({ pipelineVisibility }) {
  const candidateSearch = (pipelineVisibility || {}).candidateSearch || {};
  const trialPrefilter = (pipelineVisibility || {}).trialPrefilter || {};
  const prefilterContract = trialPrefilter.contract || {};
  const llmAdjudication = candidateSearch.llmAdjudication || {};
  const mode = candidateSearch.mode || "";
  const cohortLabel = candidateSearch.cohortLabel || "Unified patient registry";
  if (!mode) return null;
  const fallback = formatFallbackReason(candidateSearch.vectorFallbackReason);
  const prefilterTokens = [];
  if (prefilterContract?.hardFilters?.sex?.anyOf?.length) {
    prefilterTokens.push(`sex: ${prefilterContract.hardFilters.sex.anyOf.join(", ")}`);
  }
  if (prefilterContract?.hardFilters?.age && (prefilterContract.hardFilters.age.min != null || prefilterContract.hardFilters.age.max != null)) {
    const ageBits = [];
    if (prefilterContract.hardFilters.age.min != null) ageBits.push(`min ${prefilterContract.hardFilters.age.min}`);
    if (prefilterContract.hardFilters.age.max != null) ageBits.push(`max ${prefilterContract.hardFilters.age.max}`);
    prefilterTokens.push(`age: ${ageBits.join(" / ")}`);
  }

  return (
    <div className="pharma-candidate-pipeline">
      <h4>Candidate Search Pipeline</h4>
      <div className="pharma-kv-grid">
        <div><span>Mode</span><strong>{mode}</strong></div>
        <div><span>Vector Enabled</span><strong>{candidateSearch.vectorEnabled ? "Yes" : "No"}</strong></div>
        <div><span>Vector Used</span><strong>{candidateSearch.vectorUsed ? "Yes" : "No"}</strong></div>
        <div><span>Profiles embedded</span><strong>{candidateSearch.patientProfilesEmbedded ?? 0}</strong></div>
        <div><span>Vector Index</span><strong>{candidateSearch.vectorSearchIndex || "-"}</strong></div>
        <div><span>Vector Returned</span><strong>{candidateSearch.vectorReturned ?? 0}</strong></div>
        <div><span>Prefilter mode</span><strong>{trialPrefilter.mode || "deterministic hard filters"}</strong></div>
        <div><span>Prefilter candidates</span><strong>{trialPrefilter.prefilteredCandidateCount ?? "-"}</strong></div>
        <div><span>Cohort source</span><strong>{cohortLabel}</strong></div>
        <div><span>Retrieval Blend</span><strong>{candidateSearch.retrievalBlendFormula || "-"}</strong></div>
        <div><span>LLM Adjudication</span><strong>{llmAdjudication.applied ? "Applied" : "Not applied"}</strong></div>
      </div>
      {prefilterContract?.contractVersion ? (
        <p>
          Prefilter contract: <strong>{prefilterContract.contractVersion}</strong>
          {prefilterContract.hardFilterCount != null ? ` · ${prefilterContract.hardFilterCount} hard filters` : ""}
        </p>
      ) : null}
      {prefilterTokens.length > 0 ? (
        <div className="pharma-evidence-chip-row">
          {prefilterTokens.map((token) => (
            <span key={token} className="pharma-evidence-chip neutral">{token}</span>
          ))}
        </div>
      ) : null}
      {llmAdjudication.reason ? (
        <p>{llmAdjudication.reason}</p>
      ) : null}
      {candidateSearch.vectorFallbackReason ? (
        <div className="pharma-candidate-pipeline-fallback">
          {fallback.headline}
          {fallback.raw ? (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: "pointer" }}>Show raw diagnostics</summary>
              <code style={{ whiteSpace: "pre-wrap" }}>{fallback.raw}</code>
            </details>
          ) : null}
        </div>
      ) : null}
      {candidateSearch.finalBlendFormula ? (
        <p>Final candidate score formula: {candidateSearch.finalBlendFormula}</p>
      ) : null}
    </div>
  );
}
