import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import {
  APIGetPharmaControlPlane,
  APIGetPharmaPatientContext,
  APIGetRandomPharmaPatientContext,
  APIGetPharmaSourceStatus,
  APIGetSyntheaPatientEmr,
  APIRunPharmaControlPlaneEvaluation,
  APIGetPharmaTrialLlmAdjudicationStatus,
  APIListPharmaTrials,
  APIMatchPharmaTrialCandidatePatients,
  APIMatchPharmaTrials,
  APIRunPharmaTrialLlmAdjudication,
  APISyncPharmaClinicalTrials,
} from "../../services/APIService";
import BehindTheScenes from "../../components/BehindTheScenes/BehindTheScenes";
import {
  CandidateEvaluationPanel,
  ClinicalTrialMatchingPatternArticle,
  DeterministicEvidencePanel,
  EligibilityDecisionSummary,
  EligibilityCriteriaChips,
  EvidencePackSummary,
  MatchReasonHighlights,
  MatchingPipelineExplainer,
  ProcessEvidenceBoard,
  RuleEvaluationTable,
  LlmAdjudicationPanel,
} from "../../components/PharmaMatching";
import PatientClinicalRecord from "../../components/PatientClinicalRecord/PatientClinicalRecord";

const DEFAULT_SYNC_DAYS_BACK = 30;
const CLINICAL_TRIALS_SOURCE_URL = "https://clinicaltrials.gov/data-api/about-api";
const BASE_TRIAL_PHASE_VALUES = [
  "EARLY_PHASE1",
  "PHASE1",
  "PHASE1|PHASE2",
  "PHASE2",
  "PHASE2|PHASE3",
  "PHASE3",
  "PHASE4",
  "NA",
  "Not specified",
];
const DEFAULT_EXPLORER_FILTERS = {
  q: "",
  status: "all",
  phase: "all",
  source: "ctgov",
  country: "",
  sponsor: "",
};
const DEFAULT_SCREENING_FILTERS = {
  ...DEFAULT_EXPLORER_FILTERS,
  status: "RECRUITING",
  phase: "PHASE3",
};
const parseSearchParams = (search) => {
  const params = new URLSearchParams(search || "");
  return {
    appointmentId:
      params.get("appointmentId") ||
      params.get("appointmentID") ||
      "",
    patientId:
      params.get("patientId") ||
      params.get("patientID") ||
      "",
  };
};

const formatScore = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toFixed(4);
};

const formatDateTime = (value) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString();
};

const formatPhaseLabel = (value) => {
  const text = String(value || "").trim();
  if (!text || text.toLowerCase() === "not specified") return "Not specified";
  return text
    .replaceAll("|", " / ")
    .replaceAll("_", " ")
    .replace(/\bphase\s*(\d)\b/gi, "Phase $1")
    .replace(/\bearly phase\s*1\b/gi, "Early Phase 1")
    .replace(/\bna\b/gi, "N/A");
};

const formatStatusLabel = (value) => {
  return String(value || "Not specified")
    .replaceAll("_", " ")
    .trim();
};

const normalizeCriterionText = (value) => {
  const text = String(value || "").trim();
  if (!text) return "";
  return text
    .replace(/^\s*(?:[-*\u2022•]+|\d+[.)]|\(\d+\))\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
};

const inferCriterionType = (line, currentType = "inclusion") => {
  const lower = String(line || "").toLowerCase();
  if (
    /\b(exclusion criteria|exclude|must not|not eligible|ineligible|cannot|no history|without)\b/.test(lower)
  ) {
    return "exclusion";
  }
  if (/\b(inclusion criteria|eligible|must have|required)\b/.test(lower)) {
    return "inclusion";
  }
  return currentType;
};

const extractCriteriaBuckets = (trial) => {
  const inclusion = [];
  const exclusion = [];
  const seen = new Set();
  const pushUnique = (type, textValue) => {
    const normalized = normalizeCriterionText(textValue);
    if (!normalized || normalized.length < 4) return;
    const dedupeKey = `${type}::${normalized.toLowerCase()}`;
    if (seen.has(dedupeKey)) return;
    seen.add(dedupeKey);
    if (type === "exclusion") {
      exclusion.push(normalized);
    } else {
      inclusion.push(normalized);
    }
  };

  const ruleRows = Array.isArray(trial?.pharma_trial_rules)
    ? trial.pharma_trial_rules
    : Array.isArray(trial?.atomicRules)
      ? trial.atomicRules
      : [];
  if (ruleRows.length > 0) {
    ruleRows.forEach((rule) => {
      const type = String(rule?.type || "inclusion").toLowerCase() === "exclusion" ? "exclusion" : "inclusion";
      pushUnique(type, rule?.text || rule?.rule_match_text);
    });
  }

  // Fallback parser for raw text if structured rule extraction was unavailable.
  if (inclusion.length === 0 && exclusion.length === 0) {
    const rawText = String(trial?.eligibility?.rawCriteria || "");
    const rawLines = rawText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    let currentType = "inclusion";
    rawLines.forEach((line) => {
      const lower = line.toLowerCase();
      if (lower.startsWith("inclusion criteria")) {
        currentType = "inclusion";
        const remainder = line.replace(/inclusion criteria[:\s-]*/i, "");
        pushUnique("inclusion", remainder);
        return;
      }
      if (lower.startsWith("exclusion criteria")) {
        currentType = "exclusion";
        const remainder = line.replace(/exclusion criteria[:\s-]*/i, "");
        pushUnique("exclusion", remainder);
        return;
      }
      currentType = inferCriterionType(line, currentType);
      pushUnique(currentType, line);
    });
  }

  return { inclusion, exclusion };
};

function EligibilityCriteriaPanel({ trial, maxItemsPerSection = 8, className = "" }) {
  const { inclusion, exclusion } = useMemo(() => extractCriteriaBuckets(trial), [trial]);
  const [expandedSections, setExpandedSections] = useState({
    inclusion: false,
    exclusion: false,
  });
  useEffect(() => {
    setExpandedSections({ inclusion: false, exclusion: false });
  }, [trial?.trialId, trial?.nctId]);

  const inclusionRows = inclusion.slice(0, maxItemsPerSection);
  const exclusionRows = exclusion.slice(0, maxItemsPerSection);
  const inclusionExpanded = expandedSections.inclusion;
  const exclusionExpanded = expandedSections.exclusion;
  const inclusionVisibleRows = inclusionExpanded ? inclusion : inclusionRows;
  const exclusionVisibleRows = exclusionExpanded ? exclusion : exclusionRows;
  const inclusionCanExpand = inclusion.length > maxItemsPerSection;
  const exclusionCanExpand = exclusion.length > maxItemsPerSection;
  const inclusionRemaining = Math.max(0, inclusion.length - inclusionRows.length);
  const exclusionRemaining = Math.max(0, exclusion.length - exclusionRows.length);

  if (inclusion.length === 0 && exclusion.length === 0) {
    return (
      <div className={`pharma-criteria-panel ${className}`.trim()}>
        <p className="pharma-criteria-empty">No parsed inclusion/exclusion criteria available for this trial.</p>
      </div>
    );
  }

  return (
    <div className={`pharma-criteria-panel ${className}`.trim()}>
      <div className="pharma-criteria-panel-note">
        Inclusion criteria must be met. Exclusion criteria indicate reasons not to enroll.
      </div>
      <div className="pharma-criteria-columns">
        <section className="pharma-criteria-card inclusion">
          <div className="pharma-criteria-card-head">
            <div className="pharma-criteria-card-head-left">
              <span className="criteria-kind-badge inclusion">Inclusion</span>
              <span className="criteria-kind-count">{inclusion.length}</span>
            </div>
            {inclusionCanExpand ? (
              <button
                type="button"
                className="pharma-criteria-toggle"
                onClick={() => setExpandedSections((prev) => ({ ...prev, inclusion: !prev.inclusion }))}
              >
                {inclusionExpanded ? "Show less" : `Show all (${inclusion.length})`}
              </button>
            ) : null}
          </div>
          {inclusionVisibleRows.length > 0 ? (
            <div className={`pharma-criteria-table-wrap ${inclusionExpanded ? "expanded" : "collapsed"}`}>
              <table className="pharma-criteria-table">
                <thead>
                  <tr>
                    <th className="pharma-criteria-index-col">#</th>
                    <th>Criterion</th>
                  </tr>
                </thead>
                <tbody>
                  {inclusionVisibleRows.map((item, index) => (
                    <tr key={`incl-${index}`}>
                      <td className="pharma-criteria-index-col">{index + 1}</td>
                      <td className="pharma-criteria-item">{item}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="pharma-criteria-empty">No explicit inclusion criteria parsed.</p>
          )}
          {inclusionRemaining > 0 && !inclusionExpanded ? (
            <div className="pharma-criteria-more">+ {inclusionRemaining} more inclusion criteria</div>
          ) : null}
          {inclusionExpanded && inclusionCanExpand ? (
            <div className="pharma-criteria-scroll-note">Scroll to view all inclusion criteria.</div>
          ) : null}
        </section>

        <section className="pharma-criteria-card exclusion">
          <div className="pharma-criteria-card-head">
            <div className="pharma-criteria-card-head-left">
              <span className="criteria-kind-badge exclusion">Exclusion</span>
              <span className="criteria-kind-count">{exclusion.length}</span>
            </div>
            {exclusionCanExpand ? (
              <button
                type="button"
                className="pharma-criteria-toggle"
                onClick={() => setExpandedSections((prev) => ({ ...prev, exclusion: !prev.exclusion }))}
              >
                {exclusionExpanded ? "Show less" : `Show all (${exclusion.length})`}
              </button>
            ) : null}
          </div>
          {exclusionVisibleRows.length > 0 ? (
            <div className={`pharma-criteria-table-wrap ${exclusionExpanded ? "expanded" : "collapsed"}`}>
              <table className="pharma-criteria-table">
                <thead>
                  <tr>
                    <th className="pharma-criteria-index-col">#</th>
                    <th>Criterion</th>
                  </tr>
                </thead>
                <tbody>
                  {exclusionVisibleRows.map((item, index) => (
                    <tr key={`excl-${index}`}>
                      <td className="pharma-criteria-index-col">{index + 1}</td>
                      <td className="pharma-criteria-item">{item}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="pharma-criteria-empty">No explicit exclusion criteria parsed.</p>
          )}
          {exclusionRemaining > 0 && !exclusionExpanded ? (
            <div className="pharma-criteria-more">+ {exclusionRemaining} more exclusion criteria</div>
          ) : null}
          {exclusionExpanded && exclusionCanExpand ? (
            <div className="pharma-criteria-scroll-note">Scroll to view all exclusion criteria.</div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function OverviewMetricChart({ title, rows, formatLabel = (value) => value || "-", isLoading = false }) {
  const maxValue = Math.max(...rows.map((row) => Number(row?.count || 0)), 1);
  return (
    <section className="pharma-card pharma-overview-chart-card">
      <div className="pharma-overview-chart-title">
        <h2>{title}</h2>
        {!isLoading && rows.length > 0 ? (
          <span>{rows.length} categories</span>
        ) : null}
      </div>
      {isLoading ? (
        <div className="pharma-overview-loading">
          <span className="pharma-agentic-spinner" aria-hidden="true" />
          <span>Loading chart data...</span>
        </div>
      ) : rows.length === 0 ? (
        <div className="pharma-overview-loading empty">
          <span>No catalog data available for this view yet.</span>
        </div>
      ) : (
        <div className="pharma-overview-chart">
          {rows.map((row) => {
            const count = Number(row?.count || 0);
            const widthPct = Math.max(10, Math.round((count / maxValue) * 100));
            return (
              <div key={`${title}-${row?.value || row?.label}`} className="pharma-overview-chart-row">
                <div className="pharma-overview-chart-head">
                  <span>{formatLabel(row?.value || row?.label)}</span>
                  <strong>{count}</strong>
                </div>
                <div className="pharma-overview-chart-track">
                  <div className="pharma-overview-chart-bar" style={{ width: `${widthPct}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

const formatAssessment = (assessment, fallbackEligible = null) => {
  const normalized = String(assessment || "").toLowerCase();
  if (normalized === "likely_eligible") return { label: "Likely eligible", className: "likely" };
  if (normalized === "likely_ineligible") return { label: "Likely ineligible", className: "ineligible" };
  if (normalized === "needs_review") return { label: "Needs review", className: "review" };
  if (normalized === "unknown") return { label: "Needs review", className: "review" };
  if (fallbackEligible === false) return { label: "Likely ineligible", className: "ineligible" };
  return { label: "Needs review", className: "review" };
};

const formatContextStatus = (statusPayload) => {
  const status = String(statusPayload?.status || "").toLowerCase();
  if (status === "ready") return { label: "Ready", className: "likely" };
  if (status === "screening_only_ready") return { label: "Screening context ready", className: "review" };
  if (status === "needs_linkage") return { label: "Needs linkage", className: "review" };
  if (status === "ambiguous_linkage") return { label: "Ambiguous linkage", className: "review" };
  if (status === "insufficient_leafy_data") return { label: "Insufficient data", className: "ineligible" };
  return { label: "Pending context", className: "review" };
};

const KeywordOverlapChips = ({ sharedKeywords, allKeywords }) => {
  const shared = Array.isArray(sharedKeywords) ? sharedKeywords : [];
  const all = Array.isArray(allKeywords) ? allKeywords : [];
  if (shared.length === 0 && all.length === 0) return null;
  const sharedSet = new Set(shared.map((k) => String(k).toLowerCase()));
  return (
    <div className="pharma-keyword-chips">
      {all.slice(0, 12).map((kw) => {
        const isShared = sharedSet.has(String(kw).toLowerCase());
        return (
          <span key={kw} className={`pharma-keyword-chip ${isShared ? "shared" : "unmatched"}`}>
            {kw}
          </span>
        );
      })}
    </div>
  );
};


const TrialDetailOverlay = ({
  trial,
  pipelineVisibility,
  patientEvidencePack,
  onClose,
  onFindCandidates,
  candidatePayload,
  candidateLoading,
  onRunLlmAdjudication,
  llmAdjudicationLoading,
  llmAdjudicationPayload,
  flaggedCandidates,
  onToggleFlag,
  expandedCandidates,
  onToggleExpand,
}) => {
  const [activeOverlayTab, setActiveOverlayTab] = useState("overview");

  useEffect(() => {
    setActiveOverlayTab("overview");
  }, [trial?.trialId, trial?.nctId]);

  if (!trial) return null;

  const candidateSynthea = Array.isArray(candidatePayload?.syntheaCandidates) ? candidatePayload.syntheaCandidates : [];
  const candidateSummary = candidatePayload?.summary || {};
  const flagCount = flaggedCandidates?.size || 0;
  const ruleEvaluations = Array.isArray(trial.ruleEvaluations) ? trial.ruleEvaluations : [];
  const riskPaths = Array.isArray(trial?.riskEvidence?.paths) ? trial.riskEvidence.paths : [];
  const llmQueue = Array.isArray(trial?.llmAdjudicationQueue) ? trial.llmAdjudicationQueue : [];
  const deterministicUnknownRows = ruleEvaluations.filter((row) => {
    const status = String(row?.status || "").toUpperCase();
    const evalMode = String(row?.evaluationMode || "").toLowerCase();
    return (
      status === "UNKNOWN"
      && !["llm_adjudication_required", "llm_missing_citation", "llm_cited"].includes(evalMode)
    );
  });
  const deterministicUnknownCount = deterministicUnknownRows.length;
  const deterministicUnknownMissing = [
    ...new Set(
      deterministicUnknownRows.flatMap((row) => (Array.isArray(row?.missingData) ? row.missingData : []))
    ),
  ].slice(0, 8);
  const llmJobStatus = String(llmAdjudicationPayload?.jobStatus || "").toLowerCase();
  const hasNarrativeReview = llmAdjudicationLoading
    || llmQueue.length > 0
    || (Array.isArray(llmAdjudicationPayload?.executionRows) && llmAdjudicationPayload.executionRows.length > 0)
    || ["queued", "running", "completed", "failed"].includes(llmJobStatus);
  const parserWarnings = Array.isArray(trial?.parserWarnings) ? trial.parserWarnings : [];
  const validatorWarnings = Array.isArray(trial?.validatorWarnings) ? trial.validatorWarnings : [];
  const evidencePack = trial.evidencePack || patientEvidencePack || null;
  const assessment = formatAssessment(trial.overallAssessment, trial.isEligibleStructured);
  const hasPatientSpecificEvidence = Boolean(
    evidencePack
    || trial.finalScore != null
    || ruleEvaluations.length > 0
    || trial.decisionSummary
    || trial.coverageMeter?.evaluatedRules
  );

  return (
    <div className="pharma-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="pharma-overlay-content">
        {/* Header */}
        <div className="pharma-overlay-header">
          <button type="button" className="pharma-overlay-back" onClick={onClose}>
            &larr; Back to results
          </button>
          <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
            {flagCount > 0 ? <span className="pharma-flag-badge">{flagCount} flagged</span> : null}
            {trial.sourceUrl ? (
              <a href={trial.sourceUrl} target="_blank" rel="noreferrer" className="pharma-overlay-source-link">
                Source Link &rarr;
              </a>
            ) : null}
          </div>
        </div>

        {/* Title & meta */}
        <h2>{trial.title}</h2>
        <div className="pharma-overlay-meta">
          <span className="pharma-overlay-meta-chip">{trial.trialId || trial.nctId}</span>
          <span className="pharma-overlay-meta-chip">{trial.phase || "Phase N/A"}</span>
          <span className="pharma-overlay-meta-chip">{trial.status || "Not specified"}</span>
          <span className="pharma-overlay-meta-chip">{trial.sponsor || "-"}</span>
        </div>

        <div className="pharma-subtabs">
          <button
            type="button"
            className={`pharma-subtab ${activeOverlayTab === "overview" ? "active" : ""}`}
            onClick={() => setActiveOverlayTab("overview")}
          >
            Product View
          </button>
          <button
            type="button"
            className={`pharma-subtab ${activeOverlayTab === "evidence" ? "active" : ""}`}
            onClick={() => setActiveOverlayTab("evidence")}
          >
            Evidence & Trace
          </button>
        </div>

        {activeOverlayTab === "overview" ? (
          <>
            <div className="pharma-overlay-section">
              <h3>Summary</h3>
              <div className="pharma-overlay-summary">{trial.summary || "No summary available."}</div>
            </div>

            <div className="pharma-overlay-section">
              <h3>Inclusion & Exclusion Criteria</h3>
              <EligibilityCriteriaPanel trial={trial} maxItemsPerSection={12} />
            </div>

            {(trial.siteCoverage || []).length > 0 ? (
              <div className="pharma-overlay-section">
                <h3>Site Coverage ({(trial.siteCoverage || []).length} sites)</h3>
                <div className="pharma-site-list">
                  {(trial.siteCoverage || []).slice(0, 16).map((site, idx) => (
                    <div key={`site-${idx}`} className="pharma-site-item" style={{ background: "rgba(255,255,255,0.04)", borderColor: "rgba(255,255,255,0.08)" }}>
                      <strong style={{ color: "#e2e8f0" }}>{site.name || "Site"}</strong>
                      <span style={{ color: "#94a3b8" }}>{[site.city, site.state, site.country].filter(Boolean).join(", ") || "-"}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="pharma-overlay-section">
              <div className="pharma-section-note">
                This product view keeps the operational story concise. Full pipeline traces, rule-by-rule evidence, and candidate-level proofs live under Evidence & Trace.
              </div>
              <h3>Candidate Screening</h3>
              <div style={{ marginBottom: 10 }}>
                <button
                  type="button"
                  className="pharma-btn primary"
                  onClick={() => {
                    setActiveOverlayTab("evidence");
                    onFindCandidates();
                  }}
                  disabled={candidateLoading}
                >
                  {candidateLoading ? "Searching..." : "Find Candidates"}
                </button>
              </div>
              {candidatePayload ? (
                <div className="pharma-kv-grid">
                  <div><span>Cohort candidates</span><strong>{candidateSummary.syntheaCandidatesTotal ?? 0}</strong></div>
                  <div><span>Returned</span><strong>{candidateSummary.syntheaReturned ?? 0}</strong></div>
                </div>
              ) : (
                <p className="pharma-muted">Run candidate screening to generate patient-specific evidence for this trial.</p>
              )}
            </div>
          </>
        ) : null}

        {activeOverlayTab === "evidence" && hasPatientSpecificEvidence && pipelineVisibility ? (
          <div className="pharma-overlay-section">
            <h3>Execution Process</h3>
            <MatchingPipelineExplainer pipelineVisibility={pipelineVisibility} />
          </div>
        ) : null}

        {activeOverlayTab === "evidence" ? (
        <div className="pharma-overlay-section">
          <h3>Canonical Trial Contract Proof</h3>
          <DeterministicEvidencePanel row={trial} title="How raw trial criteria were transformed into a governed contract" />
        </div>
        ) : null}

        {activeOverlayTab === "evidence" && hasPatientSpecificEvidence ? (
        <div className="pharma-overlay-section">
          <h3>Why This Trial Matched This Patient</h3>
          <div className={`pharma-assessment-chip ${assessment.className}`}>{assessment.label}</div>
          <EligibilityDecisionSummary
            assessment={trial.overallAssessment}
            decisionSummary={trial.decisionSummary}
            missingCriteria={trial.missingCriteria}
          />
          <MatchReasonHighlights rules={ruleEvaluations} maxItemsPerBucket={3} />
        </div>
        ) : null}
        {activeOverlayTab === "evidence" && parserWarnings.length > 0 ? (
          <div className="pharma-overlay-section">
            <h3>Parser Warnings</h3>
            <ul className="pharma-list">
              {parserWarnings.slice(0, 8).map((warning, index) => (
                <li key={`parser-warning-${index}`}>
                  <strong>{warning?.code || "warning"}</strong>
                  <span>{warning?.message || String(warning)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {activeOverlayTab === "evidence" && validatorWarnings.length > 0 ? (
          <div className="pharma-overlay-section">
            <h3>Validator Warnings</h3>
            <ul className="pharma-list">
              {validatorWarnings.slice(0, 8).map((warning, index) => (
                <li key={`validator-warning-${index}`}>
                  <strong>{warning?.code || "warning"}</strong>
                  <span>{warning?.message || String(warning)}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {activeOverlayTab === "evidence" && ruleEvaluations.length > 0 ? (
          <div className="pharma-overlay-section">
            <h3>Rule-By-Rule Evidence</h3>
            <RuleEvaluationTable rules={ruleEvaluations} maxRows={18} />
          </div>
        ) : null}
        {activeOverlayTab === "evidence" && hasNarrativeReview ? (
        <div className="pharma-overlay-section">
          <LlmAdjudicationPanel
            queue={llmQueue}
            loading={llmAdjudicationLoading}
            adjudication={llmAdjudicationPayload}
            onRunChecks={onRunLlmAdjudication}
            maxChecks={10}
            deterministicUnknownCount={deterministicUnknownCount}
            deterministicUnknownMissing={deterministicUnknownMissing}
          />
        </div>
        ) : null}
        {activeOverlayTab === "evidence" && riskPaths.length > 0 ? (
          <div className="pharma-overlay-section">
            <h3>Risk Path Evidence</h3>
            <EligibilityCriteriaChips breakdown={riskPaths.map((path) => ({
              criterion: path.path,
              status: String(path.status || "").toLowerCase(),
              detail: Array.isArray(path.evidence) ? path.evidence.join(", ") : "",
            }))} />
          </div>
        ) : null}

        {activeOverlayTab === "evidence" && evidencePack ? (
          <div className="pharma-overlay-section">
            <h3>Patient Evidence Pack</h3>
            <EvidencePackSummary evidencePack={evidencePack} />
          </div>
        ) : null}

        {/* Scores */}
        {activeOverlayTab === "evidence" && trial.finalScore != null ? (
          <div className="pharma-overlay-section">
            <h3>Scores</h3>
            <div className="pharma-overlay-scores">
              <div className="pharma-overlay-score-box">
                <span className="score-label">Final</span>
                <span className="score-value">{formatScore(trial.finalScore)}</span>
              </div>
              <div className="pharma-overlay-score-box">
                <span className="score-label">Structured</span>
                <span className="score-value secondary">{formatScore(trial.structuredScore)}</span>
              </div>
              <div className="pharma-overlay-score-box">
                <span className="score-label">Vector</span>
                <span className="score-value secondary">{formatScore(trial.semanticScore ?? trial.vectorSearchScore)}</span>
              </div>
            </div>
          </div>
        ) : null}

        {/* Candidate Patients */}
        {activeOverlayTab === "evidence" ? (
        <div className="pharma-overlay-section">
          <h3>Candidate Patients</h3>
          <div style={{ marginBottom: 10 }}>
            <button
              type="button"
              className="pharma-btn primary"
              onClick={onFindCandidates}
              disabled={candidateLoading}
            >
              {candidateLoading ? "Searching..." : "Find Candidates"}
            </button>
          </div>
          {candidateLoading ? (
            <MatchingPipelineExplainer
              mode="candidate"
              active={candidateLoading}
              autoScrollOnActive
            />
          ) : null}
          {candidatePayload ? (
            <>
              <MatchingPipelineExplainer
                pipelineVisibility={candidatePayload.pipelineVisibility}
                mode="candidate"
                hasRun={Boolean(candidatePayload)}
              />
              <div className="pharma-kv-grid" style={{ marginBottom: 10 }}>
                <div><span>Cohort candidates</span><strong>{candidateSummary.syntheaCandidatesTotal ?? 0}</strong></div>
                <div><span>Returned</span><strong>{candidateSummary.syntheaReturned ?? 0}</strong></div>
              </div>
              <p className="pharma-muted" style={{ marginBottom: 10 }}>
                Cohort source: one governed patient registry. Deterministic prefiltering, retrieval, and evidence assembly all run on the same linked population.
              </p>
              {candidateSynthea.length > 0 ? (
                <>
                  <h3 style={{ color: "#94a3b8", fontSize: 13, marginBottom: 8 }}>Unified Patient Registry</h3>
                  <div className="pharma-table-wrap">
                    <table className="pharma-table">
                      <thead>
                        <tr>
                          <th>Patient</th>
                          <th>Score</th>
                          <th>Assessment</th>
                          <th style={{ textAlign: "center" }}>Pass</th>
                          <th style={{ textAlign: "center" }}>Fail</th>
                          <th style={{ textAlign: "center" }}>Unknown</th>
                          <th>Shared Keywords</th>
                          <th>Events</th>
                          <th>Details</th>
                          <th>Flag</th>
                        </tr>
                      </thead>
                      <tbody>
                        {candidateSynthea.map((row, idx) => {
                          const candidateKey = `synthea-${row.patientRef || idx}`;
                          const summary = row.decisionSummary || {};
                          const passCount = Number(summary.passRules || 0);
                          const failCount = Number(summary.failRules || 0);
                          const unknownCount = Number(summary.unknownRules || 0);
                          const isExpanded = expandedCandidates.has(candidateKey);
                          return (
                            <React.Fragment key={candidateKey}>
                              <tr className={isExpanded ? "pharma-row-expanded" : ""}>
                                <td>{row.patientRef || row.name || "-"}</td>
                                <td>{formatScore(row.trialFitScore)}</td>
                                <td>
                                  {(() => {
                                    const rowAssessment = formatAssessment(row.overallAssessment, row.isEligibleStructured);
                                    return <span className={`pharma-assessment-chip ${rowAssessment.className}`}>{rowAssessment.label}</span>;
                                  })()}
                                </td>
                                <td style={{ textAlign: "center" }}>
                                  <span className="pharma-rule-count pass">{passCount}</span>
                                </td>
                                <td style={{ textAlign: "center" }}>
                                  <span className="pharma-rule-count fail">{failCount}</span>
                                </td>
                                <td style={{ textAlign: "center" }}>
                                  <span className="pharma-rule-count unknown">{unknownCount}</span>
                                </td>
                                <td>
                                  {Array.isArray(row.sharedKeywords) && row.sharedKeywords.length > 0 ? (
                                    <KeywordOverlapChips sharedKeywords={row.sharedKeywords} allKeywords={row.sharedKeywords} />
                                  ) : (
                                    row.sharedKeywordCount ?? 0
                                  )}
                                </td>
                                <td>{row.resourceEventCount ?? 0}</td>
                                <td>
                                  <button
                                    type="button"
                                    className="pharma-expand-btn"
                                    onClick={() => onToggleExpand(candidateKey)}
                                  >
                                    {isExpanded ? "▼" : "▸"} Details
                                  </button>
                                </td>
                                <td>
                                  <button
                                    type="button"
                                    className={`pharma-flag-btn ${flaggedCandidates.has(candidateKey) ? "flagged" : ""}`}
                                    onClick={() => onToggleFlag(candidateKey)}
                                  >
                                    {flaggedCandidates.has(candidateKey) ? "\u2605" : "\u2606"}
                                  </button>
                                </td>
                              </tr>
                              {isExpanded ? (
                                <tr className="pharma-expanded-row">
                                  <td colSpan="10" className="pharma-expanded-cell">
                                    <CandidateEvaluationPanel row={row} expanded />
                                  </td>
                                </tr>
                              ) : null}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : null}
            </>
          ) : !candidateLoading ? (
            <p style={{ color: "#64748b", fontSize: 13 }}>
              Click <strong>Find Candidates</strong> to search for matching patients.
            </p>
          ) : null}
        </div>
        ) : null}

        {/* Behind the Scenes */}
        {activeOverlayTab === "evidence" && candidatePayload?.pipelineVisibility ? (
          <BehindTheScenes
            title="Candidate Pipeline"
            data={[{ label: "Candidate Patient Pipeline", payload: candidatePayload.pipelineVisibility }]}
          />
        ) : null}
      </div>
    </div>
  );
};

/* ── Main Component ── */

export default function PharmaNavigator() {
  const searchParams = useSearchParams();
  const routeParams = useMemo(
    () => parseSearchParams(searchParams?.toString() || ""),
    [searchParams],
  );

  const [appointmentId, setAppointmentId] = useState(routeParams.appointmentId);
  const [patientId, setPatientId] = useState(routeParams.patientId);
  const [query, setQuery] = useState("");
  const [syncWindowDays, setSyncWindowDays] = useState(DEFAULT_SYNC_DAYS_BACK);

  // Unified view state
  const [view, setView] = useState("overview"); // "overview" | "matching" | "screening"
  const [overlayTrial, setOverlayTrial] = useState(null);
  const [flaggedCandidates, setFlaggedCandidates] = useState(new Set());
  const [expandedCandidates, setExpandedCandidates] = useState(new Set());

  const [loading, setLoading] = useState(false);
  const [patientContextLoading, setPatientContextLoading] = useState(false);
  const [randomPatientLoading, setRandomPatientLoading] = useState(false);
  const [syncLoading, setSyncLoading] = useState(false);
  const [sourceStatusLoading, setSourceStatusLoading] = useState(true);
  const [trialExplorerLoading, setTrialExplorerLoading] = useState(true);
  const [trialCandidateLoading, setTrialCandidateLoading] = useState(false);
  const [llmAdjudicationLoading, setLlmAdjudicationLoading] = useState(false);
  const [llmAdjudicationPayload, setLlmAdjudicationPayload] = useState(null);

  const [error, setError] = useState("");
  const [syncSummary, setSyncSummary] = useState(null);
  const [sourceStatus, setSourceStatus] = useState(null);
  const [controlPlaneSummary, setControlPlaneSummary] = useState(null);
  const [controlPlaneLoading, setControlPlaneLoading] = useState(false);
  const [controlPlaneError, setControlPlaneError] = useState("");
  const [controlPlaneRunLoading, setControlPlaneRunLoading] = useState(false);

  const [patientContextPayload, setPatientContextPayload] = useState(null);
  const [patientEmrPayload, setPatientEmrPayload] = useState(null);
  const [patientEmrLoading, setPatientEmrLoading] = useState(false);
  const [trialMatchPayload, setTrialMatchPayload] = useState(null);
  const [trialCandidatePayload, setTrialCandidatePayload] = useState(null);
  const [hasStartedMatching, setHasStartedMatching] = useState(false);
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState("navigator");
  // selectedTrialTab removed — single-view detail panel now

  const [explorerFilters, setExplorerFilters] = useState(DEFAULT_EXPLORER_FILTERS);
  const [explorerPage, setExplorerPage] = useState(1);
  const [explorerResultsVisible, setExplorerResultsVisible] = useState(false);
  const [trialsPayload, setTrialsPayload] = useState({
    trials: [],
    totalCount: 0,
    facets: {},
    page: 1,
    limit: 12,
    pipelineVisibility: {},
  });
  const [selectedTrial, setSelectedTrial] = useState(null);

  // ── API helpers ──

  const loadSourceStatus = async () => {
    setSourceStatusLoading(true);
    try {
      const statusResponse = await APIGetPharmaSourceStatus();
      setSourceStatus(statusResponse || null);
    } finally {
      setSourceStatusLoading(false);
    }
  };

  const loadControlPlaneSummary = async () => {
    setControlPlaneLoading(true);
    setControlPlaneError("");
    try {
      const controlPlaneResponse = await APIGetPharmaControlPlane();
      setControlPlaneSummary(controlPlaneResponse?.summary || null);
    } catch (controlPlaneLoadError) {
      setControlPlaneError(
        controlPlaneLoadError?.response?.data?.error
          || controlPlaneLoadError?.message
          || "Process evidence is temporarily unavailable."
      );
      throw controlPlaneLoadError;
    } finally {
      setControlPlaneLoading(false);
    }
  };

  const runControlPlaneEvaluation = async (mode = "runtime_snapshot") => {
    setControlPlaneRunLoading(true);
    setControlPlaneError("");
    try {
      const response = await APIRunPharmaControlPlaneEvaluation({ mode });
      setControlPlaneSummary(response?.summary || null);
    } catch (controlPlaneRunError) {
      setControlPlaneError(
        controlPlaneRunError?.response?.data?.error
          || controlPlaneRunError?.message
          || "Control-plane evaluation failed."
      );
    } finally {
      setControlPlaneRunLoading(false);
    }
  };

  const loadTrialExplorer = async (nextPage = 1) => {
    setTrialExplorerLoading(true);
    try {
      const response = await APIListPharmaTrials({
        ...explorerFilters,
        page: nextPage,
        limit: 12,
        includeFacets: true,
        sortField: "sourceStudyLastUpdated",
        sortOrder: "desc",
      });
      const payload = {
        trials: Array.isArray(response?.trials) ? response.trials : [],
        totalCount: Number(response?.totalCount || 0),
        facets: response?.facets || {},
        page: Number(response?.page || nextPage),
        limit: Number(response?.limit || 12),
        pipelineVisibility: response?.pipelineVisibility || {},
      };
      setTrialsPayload(payload);
      setExplorerPage(payload.page);
      if (payload.trials.length > 0) {
        setSelectedTrial((current) => {
          const currentId = current?.trialId || current?.nctId;
          const existing = payload.trials.find((trialRow) => {
            const trialId = trialRow?.trialId || trialRow?.nctId;
            return trialId && trialId === currentId;
          });
          return existing || null;
        });
      } else {
        setSelectedTrial(null);
      }
    } finally {
      setTrialExplorerLoading(false);
    }
  };

  const loadTrialOverviewSummary = async () => {
    setTrialExplorerLoading(true);
    try {
      const response = await APIListPharmaTrials({
        ...explorerFilters,
        page: 1,
        limit: 1,
        includeFacets: true,
        summaryOnly: true,
        sortField: "sourceStudyLastUpdated",
        sortOrder: "desc",
      });
      setTrialsPayload((previous) => ({
        ...previous,
        trials: [],
        totalCount: Number(response?.totalCount || 0),
        facets: response?.facets || {},
        page: 1,
        limit: Number(response?.limit || 1),
        pipelineVisibility: response?.pipelineVisibility || {},
      }));
    } finally {
      setTrialExplorerLoading(false);
    }
  };

  const runClinicalTrialsSync = async (daysBack = syncWindowDays) => {
    setSyncLoading(true);
    setError("");
    try {
      const response = await APISyncPharmaClinicalTrials({
        condition: "breast cancer",
        statuses: ["RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING"],
        daysBack,
        maxStudies: 4000,
      });
      setSyncSummary(response || null);
      const followUpTasks = [
        loadSourceStatus(),
        view === "screening" ? loadTrialExplorer(1) : loadTrialOverviewSummary(),
      ];
      if (activeWorkspaceTab === "evidence" || controlPlaneSummary) {
        followUpTasks.push(loadControlPlaneSummary());
      }
      await Promise.allSettled(followUpTasks);
    } catch (syncError) {
      setError(syncError?.response?.data?.error || syncError?.message || "ClinicalTrials sync failed");
    } finally {
      setSyncLoading(false);
    }
  };

  const loadRandomPatientContext = async () => {
    setRandomPatientLoading(true);
    setError("");
    try {
      const response = await APIGetRandomPharmaPatientContext();
      setAppointmentId(response?.appointmentId || "");
      setPatientId(response?.patientId || "");
      setPatientContextPayload(response || null);
      const semanticQuery = response?.patientContext?.semanticQuery || "";
      if (semanticQuery) {
        setQuery(semanticQuery);
      }
      setTrialMatchPayload(null);
      setHasStartedMatching(false);
      await loadLinkedPatientEmr(response || null);
      setView("matching");
    } catch (randomError) {
      setPatientEmrPayload(null);
      setError(
        randomError?.response?.data?.error
          || randomError?.message
          || "Failed to load a random screening patient"
      );
    } finally {
      setRandomPatientLoading(false);
    }
  };

  const loadSelectedPatientContext = async () => {
    if (!appointmentId) {
      setError("Enter an appointment ID to load the patient context.");
      return;
    }
    setPatientContextLoading(true);
    setError("");
    setTrialMatchPayload(null);
    setHasStartedMatching(false);
    setPatientEmrPayload(null);
    try {
      const response = await APIGetPharmaPatientContext(appointmentId, patientId || null);
      setPatientContextPayload(response || null);
      const semanticQuery = response?.patientContext?.semanticQuery || "";
      if (!query?.trim() && semanticQuery) {
        setQuery(semanticQuery);
      }
      await loadLinkedPatientEmr(response || null);
      setView("matching");
    } catch (contextError) {
      setPatientContextPayload(null);
      setPatientEmrPayload(null);
      setError(
        contextError?.response?.data?.error
          || contextError?.message
          || "Failed to load patient clinical information"
      );
    } finally {
      setPatientContextLoading(false);
    }
  };

  const loadLinkedPatientEmr = async (contextResponse) => {
    const payload = contextResponse && typeof contextResponse === "object" ? contextResponse : {};
    const linkedPatientRef =
      payload?.patientLink?.syntheaPatientRef
      || payload?.patientContext?.syntheaPatientRef
      || "";
    if (!linkedPatientRef) {
      setPatientEmrPayload(null);
      setPatientEmrLoading(false);
      return;
    }

    setPatientEmrLoading(true);
    try {
      const emrResponse = await APIGetSyntheaPatientEmr(linkedPatientRef, 120);
      setPatientEmrPayload(emrResponse || null);
    } catch (emrError) {
      setPatientEmrPayload({
        success: false,
        error:
          emrError?.response?.data?.error
          || emrError?.message
          || "Unable to load linked patient EMR",
        patientRef: linkedPatientRef,
        resources: [],
        timeline: [],
        domains: {},
        summary: {},
        patient: {},
      });
    } finally {
      setPatientEmrLoading(false);
    }
  };

  const runMatchingFlow = async () => {
    if (!patientContextPayload?.patientContext && !appointmentId) {
      setError("Select a patient first.");
      return;
    }
    setLoading(true);
    setHasStartedMatching(true);
    setTrialMatchPayload(null);
    setError("");
    try {
      const contextResponse = patientContextPayload?.patientContext
        ? patientContextPayload
        : await APIGetPharmaPatientContext(appointmentId, patientId || null);
      setPatientContextPayload(contextResponse || null);
      if (!patientEmrPayload && contextResponse) {
        await loadLinkedPatientEmr(contextResponse);
      }

      const effectiveQuery = query?.trim() || contextResponse?.patientContext?.semanticQuery || "";
      if (!query?.trim() && effectiveQuery) {
        setQuery(effectiveQuery);
      }

      const [matchResult, controlPlaneResult] = await Promise.allSettled([
        APIMatchPharmaTrials({
          appointmentId,
          patientId: patientId || undefined,
          query: effectiveQuery,
          trialFilters: explorerFilters,
          topK: 8,
          rerankEnabled: true,
        }),
        APIGetPharmaControlPlane(),
      ]);

      if (matchResult.status !== "fulfilled") {
        throw matchResult.reason;
      }

      setTrialMatchPayload(matchResult.value || null);
      if (controlPlaneResult.status === "fulfilled") {
        setControlPlaneSummary(controlPlaneResult.value?.summary || null);
      }
      setView("matching");
    } catch (matchError) {
      setHasStartedMatching(false);
      setError(matchError?.response?.data?.error || matchError?.message || "Failed to run matching flow");
    } finally {
      setLoading(false);
    }
  };

  const runSelectedTrialCandidateMatch = useCallback(async (trialId) => {
    const targetId = trialId || overlayTrial?.trialId || selectedTrial?.trialId;
    if (!targetId) {
      setError("Select a trial first.");
      return;
    }
    setTrialCandidateLoading(true);
    setTrialCandidatePayload(null);
    setError("");
    try {
      const response = await APIMatchPharmaTrialCandidatePatients({
        trialId: targetId,
        topK: 12,
      });
      setTrialCandidatePayload(response || null);
    } catch (candidateError) {
      setError(
        candidateError?.response?.data?.error
          || candidateError?.message
          || "Failed to match selected trial to patient candidates"
      );
    } finally {
      setTrialCandidateLoading(false);
    }
  }, [overlayTrial, selectedTrial]);

  const applyLlmUpdatedMatch = useCallback((updatedMatch) => {
    if (!updatedMatch || !updatedMatch.trialId) return;
    setOverlayTrial((current) => {
      if (!current) return current;
      const currentId = current.trialId || current.nctId;
      if (currentId !== updatedMatch.trialId && currentId !== updatedMatch.nctId) return current;
      return { ...current, ...updatedMatch };
    });
    setTrialMatchPayload((previous) => {
      if (!previous || !Array.isArray(previous.matches)) return previous;
      const updatedRows = previous.matches.map((row) => {
        if (row.trialId === updatedMatch.trialId || row.nctId === updatedMatch.nctId) {
          return { ...row, ...updatedMatch };
        }
        return row;
      });
      return { ...previous, matches: updatedRows };
    });
  }, []);

  const runOverlayTrialLlmAdjudication = useCallback(async () => {
    const targetTrial = overlayTrial?.trialId || overlayTrial?.nctId;
    if (!targetTrial) {
      setError("Select a trial first.");
      return;
    }
    if (!appointmentId) {
      setError("Appointment ID is required to adjudicate patient-specific rules.");
      return;
    }
    setLlmAdjudicationLoading(true);
    setError("");
    try {
      const startResponse = await APIRunPharmaTrialLlmAdjudication({
        trialId: targetTrial,
        appointmentId,
        patientId: patientId || undefined,
        query: query || undefined,
        maxChecks: 10,
        parallelism: 10,
        asyncMode: true,
      });

      if (startResponse?.updatedMatch) {
        setLlmAdjudicationPayload(startResponse?.adjudication || null);
        applyLlmUpdatedMatch(startResponse.updatedMatch);
        return;
      }

      const job = startResponse?.job || {};
      const jobId = job?.jobId;
      if (!jobId) {
        throw new Error("LLM adjudication job did not return a jobId.");
      }
      setLlmAdjudicationPayload({
        ...(job?.progress || {}),
        jobStatus: job?.status || "queued",
        jobId,
      });

      const startedAt = Date.now();
      const timeoutMs = 180000;
      while (Date.now() - startedAt < timeoutMs) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        const statusResponse = await APIGetPharmaTrialLlmAdjudicationStatus(jobId);
        const statusJob = statusResponse?.job || {};
        const statusValue = String(statusJob?.status || "").toLowerCase();
        const statusProgress = statusJob?.progress || {};
        setLlmAdjudicationPayload({
          ...statusProgress,
          jobStatus: statusValue || "running",
          jobId,
          error: statusJob?.error || null,
        });

        if (statusValue === "completed") {
          const finalResult = statusResponse?.result || {};
          const finalAdjudication = finalResult?.adjudication || {};
          setLlmAdjudicationPayload({
            ...finalAdjudication,
            jobStatus: "completed",
            jobId,
          });
          applyLlmUpdatedMatch(finalResult?.updatedMatch);
          return;
        }
        if (statusValue === "failed") {
          throw new Error(statusJob?.error || "LLM adjudication job failed.");
        }
      }
      throw new Error("LLM adjudication timed out while polling status.");
    } catch (adjudicationError) {
      setError(
        adjudicationError?.response?.data?.error
        || adjudicationError?.message
        || "Failed to run LLM adjudication"
      );
    } finally {
      setLlmAdjudicationLoading(false);
    }
  }, [overlayTrial, appointmentId, patientId, query, applyLlmUpdatedMatch]);

  const toggleFlag = useCallback((candidateId) => {
    setFlaggedCandidates((prev) => {
      const next = new Set(prev);
      if (next.has(candidateId)) {
        next.delete(candidateId);
      } else {
        next.add(candidateId);
      }
      return next;
    });
  }, []);

  const toggleCandidateExpansion = useCallback((candidateId) => {
    setExpandedCandidates((prev) => {
      const next = new Set(prev);
      if (next.has(candidateId)) {
        next.delete(candidateId);
      } else {
        next.add(candidateId);
      }
      return next;
    });
  }, []);

  // ── Effects ──

  useEffect(() => {
    setAppointmentId(routeParams.appointmentId);
    setPatientId(routeParams.patientId);
    setPatientContextPayload(null);
    setPatientEmrPayload(null);
    setTrialMatchPayload(null);
    setHasStartedMatching(false);
  }, [routeParams.appointmentId, routeParams.patientId]);

  useEffect(() => {
    let canceled = false;
    const initialize = async () => {
      try {
        await Promise.allSettled([loadSourceStatus(), loadTrialOverviewSummary()]);
      } catch (initError) {
        if (!canceled) {
          setError(initError?.response?.data?.error || initError?.message || "Failed to initialize source status");
        }
      }
    };
    initialize();
    return () => {
      canceled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeWorkspaceTab !== "evidence") return;
    if (controlPlaneSummary || controlPlaneLoading) return;
    loadControlPlaneSummary().catch((controlPlaneLoadError) => {
      setControlPlaneError(
        controlPlaneLoadError?.response?.data?.error
          || controlPlaneLoadError?.message
          || "Process evidence is temporarily unavailable."
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspaceTab, controlPlaneSummary, controlPlaneLoading]);

  useEffect(() => {
    if (view !== "screening") return;
    if (trialExplorerLoading) return;
    if (Array.isArray(trialsPayload.trials) && trialsPayload.trials.length > 0) return;
    loadTrialExplorer(1).catch((loadError) => {
      setError(loadError?.response?.data?.error || loadError?.message || "Failed to load trial catalog");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // Tab reset removed — single-view detail panel now

  // ── Explorer helpers ──

  const handleExplorerFilterChange = (fieldName, fieldValue) => {
    setExplorerFilters((prev) => ({
      ...prev,
      [fieldName]: fieldValue,
    }));
    setTrialMatchPayload(null);
    setHasStartedMatching(false);
  };

  const resetExplorerFilters = () => {
    setExplorerFilters(view === "screening" ? DEFAULT_SCREENING_FILTERS : DEFAULT_EXPLORER_FILTERS);
    setExplorerResultsVisible(false);
    setSelectedTrial(null);
    setTrialMatchPayload(null);
    setHasStartedMatching(false);
  };

  const openMatchingView = () => {
    setView("matching");
  };

  const openScreeningView = () => {
    setExplorerFilters(DEFAULT_SCREENING_FILTERS);
    setExplorerResultsVisible(false);
    setSelectedTrial(null);
    setTrialCandidatePayload(null);
    setExpandedCandidates(new Set());
    setFlaggedCandidates(new Set());
    setView("screening");
  };

  const applyExplorerFilters = async () => {
    setError("");
    setTrialMatchPayload(null);
    setHasStartedMatching(false);
    try {
      if (view === "screening") {
        await loadTrialExplorer(1);
      } else {
        await loadTrialOverviewSummary();
      }
      setExplorerResultsVisible(true);
    } catch (filterError) {
      setError(filterError?.response?.data?.error || filterError?.message || "Failed to load trial explorer");
    }
  };

  const handlePageChange = async (delta) => {
    const nextPage = Math.max(1, explorerPage + delta);
    const maxPage = Math.max(1, Math.ceil((trialsPayload.totalCount || 0) / (trialsPayload.limit || 12)));
    if (nextPage > maxPage) {
      return;
    }
    setError("");
    try {
      await loadTrialExplorer(nextPage);
    } catch (pageError) {
      setError(pageError?.response?.data?.error || pageError?.message || "Failed to load page");
    }
  };

  const openTrialOverlay = (trial) => {
    setTrialCandidatePayload(null);
    setLlmAdjudicationPayload(null);
    setOverlayTrial(trial);
  };

  // ── Derived state ──

  const sourceLocal = sourceStatus?.local || {};
  const sourceExternal = sourceStatus?.external || {};
  const matchRows = Array.isArray(trialMatchPayload?.matches) ? trialMatchPayload.matches : [];
  const patientContextStatus = trialMatchPayload?.patientContextStatus || patientContextPayload?.patientContextStatus || null;
  const pipelineVisibility = trialMatchPayload?.pipelineVisibility || {};
  const hasMatchingContext = Boolean(trialMatchPayload || patientContextPayload);
  const patientContextReady = Boolean(patientContextPayload?.patientContext);
  const matchingResultsVisible = hasStartedMatching && !loading && Boolean(trialMatchPayload);
  const showActiveTrialsList = hasMatchingContext || explorerResultsVisible || (trialsPayload.trials || []).length > 0;
  const totalPages = Math.max(1, Math.ceil((trialsPayload.totalCount || 0) / (trialsPayload.limit || 12)));
  const processBannerLabel = controlPlaneSummary
    ? "Process evidence and governance is active below."
    : controlPlaneLoading
      ? "Loading process evidence and governance summary..."
      : "Process evidence summary is not available yet.";
  const phaseOptions = useMemo(() => {
    const facetRows = Array.isArray(trialsPayload.facets?.phase) ? trialsPayload.facets.phase : [];
    const optionMap = new Map();

    BASE_TRIAL_PHASE_VALUES.forEach((phaseValue) => {
      optionMap.set(String(phaseValue), { value: String(phaseValue), count: null });
    });

    facetRows.forEach((row) => {
      const rawValue = row?.value;
      const optionValue = (rawValue === null || rawValue === undefined || String(rawValue).trim() === "")
        ? "Not specified"
        : String(rawValue);
      optionMap.set(optionValue, {
        value: optionValue,
        count: Number.isFinite(Number(row?.count)) ? Number(row.count) : null,
      });
    });

    return Array.from(optionMap.values());
  }, [trialsPayload.facets]);
  const statusOverviewRows = useMemo(
    () => (
      Array.isArray(trialsPayload.facets?.status)
        ? [...trialsPayload.facets.status]
            .sort((left, right) => formatStatusLabel(left?.value).localeCompare(formatStatusLabel(right?.value), undefined, { sensitivity: "base" }))
            .slice(0, 6)
        : []
    ),
    [trialsPayload.facets]
  );
  const phaseOverviewRows = useMemo(
    () => (
      Array.isArray(trialsPayload.facets?.phase)
        ? [...trialsPayload.facets.phase]
            .sort((left, right) => formatPhaseLabel(left?.value).localeCompare(formatPhaseLabel(right?.value), undefined, { sensitivity: "base" }))
            .slice(0, 6)
        : []
    ),
    [trialsPayload.facets]
  );
  const countryOptions = useMemo(() => {
    const facetRows = Array.isArray(trialsPayload.facets?.country) ? trialsPayload.facets.country : [];
    return [
      { value: "", count: null },
      ...facetRows
        .map((row) => ({
          value: String(row?.value || "").trim(),
          count: Number.isFinite(Number(row?.count)) ? Number(row.count) : null,
        }))
        .filter((row) => row.value),
    ];
  }, [trialsPayload.facets]);
  const activeTrialFilterTokens = useMemo(() => {
    const tokens = [];
    if (explorerFilters.status && explorerFilters.status !== "all") {
      tokens.push(`status:${explorerFilters.status}`);
    }
    if (explorerFilters.phase && explorerFilters.phase !== "all") {
      tokens.push(`phase:${formatPhaseLabel(explorerFilters.phase)}`);
    }
    if (explorerFilters.source && explorerFilters.source !== "all") {
      tokens.push(`source:${explorerFilters.source === "ctgov" ? "ClinicalTrials.gov" : explorerFilters.source}`);
    }
    if (explorerFilters.country) {
      tokens.push(`country:${explorerFilters.country}`);
    }
    if (explorerFilters.sponsor) {
      tokens.push(`sponsor:${explorerFilters.sponsor}`);
    }
    if (explorerFilters.q) {
      tokens.push(`query:${explorerFilters.q}`);
    }
    return tokens;
  }, [explorerFilters]);
  const catalogLastUpdated =
    sourceExternal?.dataTimestamp
    || sourceLocal?.sources?.[0]?.latestFetchedAt
    || sourceLocal?.sources?.[0]?.latestSourceDate
    || null;
  const hasCatalogFilters = activeTrialFilterTokens.length > 0;
  const overviewHeaderLoading = sourceStatusLoading;
  const overviewChartsLoading = trialExplorerLoading;
  const recruitingOverviewCount = Number(
    (statusOverviewRows.find((row) => String(row?.value || "").toUpperCase() === "RECRUITING") || {}).count || 0
  );
  const recruitingHeaderCount = useMemo(() => {
    const directCount = Number(sourceLocal?.recruitingTrials);
    if (Number.isFinite(directCount) && directCount > 0) {
      return directCount;
    }
    if (recruitingOverviewCount > 0) {
      return recruitingOverviewCount;
    }
    return Number.isFinite(directCount) ? directCount : 0;
  }, [sourceLocal?.recruitingTrials, recruitingOverviewCount]);
  const patientContextDisplayStatus = formatContextStatus(patientContextStatus);
  const patientContextPreflightTone = patientContextDisplayStatus.className === "likely"
    ? "pass"
    : patientContextDisplayStatus.className === "ineligible"
      ? "blocked"
      : "review";

  // ── Render ──

  return (
    <div className="pharma-page">

      {/* ── Header ── */}
      <div className="pharma-header">
        <div className="pharma-header-left">
          <h1>Clinical Trials Navigator</h1>
          <p>
            Discover clinical trials, match patients to studies, and explore cohort intelligence.
          </p>
        </div>
        <div className="pharma-header-stats">
          <div className="pharma-header-stat">
            <span>Recruiting</span>
            <strong>
              {overviewHeaderLoading ? (
                <span className="pharma-stat-loading">
                  <span className="pharma-agentic-spinner" aria-hidden="true" />
                </span>
              ) : recruitingHeaderCount}
            </strong>
          </div>
          <div className="pharma-header-stat">
            <span>Catalog Size</span>
            <strong>
              {overviewHeaderLoading ? (
                <span className="pharma-stat-loading">
                  <span className="pharma-agentic-spinner" aria-hidden="true" />
                </span>
              ) : (sourceLocal?.totalTrials ?? trialsPayload.totalCount ?? 0)}
            </strong>
          </div>
          <div className="pharma-header-stat">
            <span>Last Update</span>
            <strong style={{ fontSize: 13 }}>
              {overviewHeaderLoading ? (
                <span className="pharma-stat-loading">
                  <span className="pharma-agentic-spinner" aria-hidden="true" />
                </span>
              ) : formatDateTime(catalogLastUpdated)}
            </strong>
          </div>
        </div>
      </div>

      <div className="pharma-workspace-tabs">
        <button
          type="button"
          className={`pharma-workspace-tab ${activeWorkspaceTab === "navigator" ? "active" : ""}`}
          onClick={() => setActiveWorkspaceTab("navigator")}
        >
          Navigator
        </button>
        <button
          type="button"
          className={`pharma-workspace-tab ${activeWorkspaceTab === "evidence" ? "active" : ""}`}
          onClick={() => setActiveWorkspaceTab("evidence")}
        >
          Control Plane
        </button>
        <button
          type="button"
          className={`pharma-workspace-tab ${activeWorkspaceTab === "pattern" ? "active" : ""}`}
          onClick={() => setActiveWorkspaceTab("pattern")}
        >
          Pattern
        </button>
      </div>

      {activeWorkspaceTab === "evidence" ? (
        <div className="pharma-step-section">
          {controlPlaneSummary ? (
            <ProcessEvidenceBoard
              summary={controlPlaneSummary}
              onRunEvaluation={runControlPlaneEvaluation}
              runEvaluationLoading={controlPlaneRunLoading}
            />
          ) : (
            <section className="pharma-card pharma-process-board pharma-process-board-placeholder">
              <div className="pharma-process-board-head">
                <div>
                  <h2>Process Evidence & Governance</h2>
                  <p className="pharma-muted">{processBannerLabel}</p>
                </div>
                <div className="pharma-evidence-chip-row">
                  <span className={`pharma-evidence-chip ${controlPlaneLoading ? "neutral" : "review"}`}>
                    {controlPlaneLoading ? "loading control plane" : "control plane unavailable"}
                  </span>
                </div>
              </div>
              <div className="pharma-process-placeholder-body">
                <p>
                  This section shows dataset readiness, linkage posture, deterministic rule coverage, and the current backlog.
                </p>
                {controlPlaneError ? (
                  <div className="pharma-candidate-pipeline-fallback">
                    {controlPlaneError}
                  </div>
                ) : null}
              </div>
            </section>
          )}

          <div className="pharma-evidence-workspace-grid">
            <section className="pharma-card">
              <h2>Vector & Retrieval Readiness</h2>
              <div className="pharma-kv-grid">
                <div><span>Patient profiles</span><strong>{sourceLocal?.patientProfiles ?? 0}</strong></div>
                <div><span>Patient profiles embedded</span><strong>{sourceLocal?.patientProfilesEmbedded ?? 0}</strong></div>
                <div><span>Trials embedded</span><strong>{sourceLocal?.trialsEmbedded ?? 0}</strong></div>
                <div><span>Criteria embedded</span><strong>{sourceLocal?.trialsCriteriaEmbedded ?? 0}</strong></div>
              </div>
              <p className="pharma-muted" style={{ marginTop: 10 }}>
                Retrieval can improve recall, but deterministic rule evaluation remains the final eligibility surface.
              </p>
            </section>
            <section className="pharma-card">
              <h2>Live Runtime Context</h2>
              <div className="pharma-kv-grid">
                <div><span>Workspace mode</span><strong>{view}</strong></div>
                <div><span>Selected trial</span><strong>{selectedTrial?.trialId || "-"}</strong></div>
                <div><span>Patient context</span><strong>{patientContextStatus?.status || "-"}</strong></div>
                <div><span>Match results</span><strong>{matchRows.length}</strong></div>
              </div>
              <p className="pharma-muted" style={{ marginTop: 10 }}>
                This tab is the proof surface. The navigator tab stays focused on workflow and outcomes.
              </p>
            </section>
          </div>

          {(trialMatchPayload?.pipelineVisibility || trialCandidatePayload?.pipelineVisibility || trialsPayload?.pipelineVisibility) ? (
            <BehindTheScenes
              title="Trial Matching Evidence"
              data={[
                ...(trialMatchPayload?.pipelineVisibility ? [{ label: "Patient To Trials", payload: trialMatchPayload.pipelineVisibility }] : []),
                ...(trialCandidatePayload?.pipelineVisibility ? [{ label: "Trial To Patients", payload: trialCandidatePayload.pipelineVisibility }] : []),
                ...(trialsPayload?.pipelineVisibility ? [{ label: "Trial Explorer", payload: trialsPayload.pipelineVisibility }] : []),
              ]}
            />
          ) : null}
        </div>
      ) : null}

      {activeWorkspaceTab === "pattern" ? (
        <div className="pharma-step-section">
          <ClinicalTrialMatchingPatternArticle />
        </div>
      ) : null}

      {activeWorkspaceTab === "navigator" ? (
      <>
      {view === "overview" ? (
        <div className="pharma-action-bar">
          <div className="pharma-action-bar-group">
            <span className="pharma-action-bar-stat">
              API: <strong>{sourceExternal?.reachable ? "Connected" : "Unavailable"}</strong>
            </span>
            <span className="pharma-action-bar-stat">
              Source:{" "}
              <a
                href={CLINICAL_TRIALS_SOURCE_URL}
                target="_blank"
                rel="noreferrer"
                className="pharma-action-bar-link"
              >
                ClinicalTrials.gov API
              </a>
            </span>
            <span className="pharma-action-bar-stat">
              Data: <strong>{formatDateTime(sourceExternal?.dataTimestamp)}</strong>
            </span>
          </div>
          <div className="pharma-action-bar-group pharma-action-bar-group-right">
            <div className="pharma-field compact-field" style={{ minWidth: 120 }}>
              <select
                value={syncWindowDays}
                onChange={(event) => setSyncWindowDays(Number(event.target.value || DEFAULT_SYNC_DAYS_BACK))}
                style={{ padding: "5px 8px", fontSize: 12 }}
              >
                <option value={1}>Last day</option>
                <option value={7}>Last 7 days</option>
                <option value={30}>Last month</option>
              </select>
            </div>
            <button
              type="button"
              className="pharma-btn primary"
              onClick={() => runClinicalTrialsSync(syncWindowDays)}
              disabled={syncLoading}
              style={{ fontSize: 12, padding: "6px 12px" }}
            >
              {syncLoading ? "Syncing..." : "Sync New/Updated Trials"}
            </button>
            {syncSummary ? (
              <span className="pharma-muted pharma-action-bar-footnote" style={{ fontSize: 11 }}>
                Last sync: {syncSummary?.write?.upsertedCount ?? 0} new, {syncSummary?.write?.modifiedCount ?? 0} updated
                {syncSummary?.write?.embeddedCount != null ? `, ${syncSummary.write.embeddedCount} embedded` : ""}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* ── Error ── */}
      {error ? <div className="pharma-error">{error}</div> : null}

      {view === "overview" ? (
        <div className="pharma-step-section">
          {/* ── Primary actions ── */}
          <div className="pharma-actions-hero">
            <button
              type="button"
              className="pharma-action-hero-card"
              onClick={openMatchingView}
            >
              <span className="pharma-action-hero-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#00684A" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
              </span>
              <div className="pharma-action-hero-body">
                <span className="pharma-action-hero-kicker">Patient workflow</span>
                <strong>Match Clinical Trials For One Patient</strong>
                <span>Select a patient, filter the trial catalog, then run deterministic eligibility matching enriched with vector search.</span>
              </div>
              <span className="pharma-action-hero-btn">Open Patient-To-Trials <span>&rarr;</span></span>
            </button>
            <button
              type="button"
              className="pharma-action-hero-card"
              onClick={openScreeningView}
            >
              <span className="pharma-action-hero-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#00684A" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              </span>
              <div className="pharma-action-hero-body">
                <span className="pharma-action-hero-kicker">Trial workflow</span>
                <strong>Match Patients To One Clinical Trial</strong>
                <span>Choose one trial, then screen the governed patient registry against it to find candidate patients for enrollment.</span>
              </div>
              <span className="pharma-action-hero-btn">Open Trial-To-Patients <span>&rarr;</span></span>
            </button>
          </div>

          {/* ── Catalog charts ── */}
          <div className="pharma-overview-grid">
            <OverviewMetricChart
              title="Status Overview"
              rows={statusOverviewRows}
              formatLabel={(value) => formatStatusLabel(value)}
              isLoading={overviewChartsLoading}
            />
            <OverviewMetricChart
              title="Phase Overview"
              rows={phaseOverviewRows}
              formatLabel={(value) => formatPhaseLabel(value)}
              isLoading={overviewChartsLoading}
            />
          </div>
        </div>
      ) : null}

      {view === "matching" ? (
        <div className="pharma-step-section">
          <div className="pharma-view-header">
            <button type="button" className="pharma-back-link" onClick={() => setView("overview")}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
              Back
            </button>
            <h2>Match Clinical Trials For One Patient</h2>
            <p className="pharma-muted">Select a patient, filter the trial catalog, then run deterministic eligibility matching.</p>
          </div>
          <section className="pharma-card" style={{ marginBottom: 14 }}>
            <div className="pharma-step-label"><span className="pharma-step-num">1</span> Select Patient</div>
            <div className="pharma-input-panel">
              <div className="pharma-field">
                <label>Appointment ID</label>
                <input
                  value={appointmentId}
                  onChange={(event) => {
                    setAppointmentId(event.target.value);
                    setPatientContextPayload(null);
                    setTrialMatchPayload(null);
                    setHasStartedMatching(false);
                  }}
                  placeholder="Leafy appointment id"
                />
              </div>
              <div className="pharma-field">
                <label>Patient ID (optional)</label>
                <input
                  value={patientId}
                  onChange={(event) => {
                    setPatientId(event.target.value);
                    setPatientContextPayload(null);
                    setTrialMatchPayload(null);
                    setHasStartedMatching(false);
                  }}
                  placeholder="Leafy patient id"
                />
              </div>
              <div className="pharma-field grow">
                <label>Intent query</label>
                <input
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setTrialMatchPayload(null);
                    setHasStartedMatching(false);
                  }}
                  placeholder="e.g. high-risk BI-RADS 4 suspicious calcifications urgent biopsy"
                />
              </div>
            </div>
            <div className="pharma-row-actions">
              <button
                type="button"
                className="pharma-btn primary"
                onClick={loadSelectedPatientContext}
                disabled={patientContextLoading || randomPatientLoading}
              >
                {patientContextLoading ? "Loading patient..." : "Load Patient Clinical Information"}
              </button>
              <button
                type="button"
                className="pharma-btn secondary"
                onClick={loadRandomPatientContext}
                disabled={patientContextLoading || randomPatientLoading}
              >
                {randomPatientLoading ? "Selecting..." : "Load Random Patient"}
              </button>
            </div>
            {patientContextPayload?.patientContext ? (
              <div className="pharma-patient-summary-wrap">
                <PatientClinicalRecord
                  patientContext={patientContextPayload.patientContext}
                  evidencePack={patientContextPayload?.evidencePack || trialMatchPayload?.patientEvidencePack}
                  patientContextStatus={patientContextStatus}
                  reportDoc={patientContextPayload?.report}
                  appointmentDoc={patientContextPayload?.appointment}
                  patientLink={patientContextPayload?.patientLink}
                  emrPayload={patientEmrPayload}
                  isLoading={patientEmrLoading}
                />
              </div>
            ) : (
              <div className="pharma-setup-placeholder">
                Load a patient first. This step prepares the full reusable patient clinical record used by trial matching.
              </div>
            )}
          </section>

          <section className="pharma-card" style={{ marginBottom: 14 }}>
            <div className="pharma-step-label"><span className="pharma-step-num">2</span> Filter Trial Set</div>
            <div className="pharma-filter-grid">
              <div className="pharma-field">
                <label>Search text</label>
                <input
                  value={explorerFilters.q}
                  onChange={(event) => handleExplorerFilterChange("q", event.target.value)}
                  placeholder="keyword, sponsor, condition..."
                />
              </div>
              <div className="pharma-field">
                <label>Status</label>
                <select
                  value={explorerFilters.status}
                  onChange={(event) => handleExplorerFilterChange("status", event.target.value)}
                >
                  <option value="all">All</option>
                  <option value="RECRUITING">Recruiting</option>
                  <option value="ACTIVE_NOT_RECRUITING">Active, not recruiting</option>
                  <option value="NOT_YET_RECRUITING">Not yet recruiting</option>
                  <option value="ENROLLING_BY_INVITATION">Enrolling by invitation</option>
                  <option value="TERMINATED">Terminated</option>
                </select>
              </div>
              <div className="pharma-field">
                <label>Phase</label>
                <select
                  value={explorerFilters.phase}
                  onChange={(event) => handleExplorerFilterChange("phase", event.target.value)}
                >
                  <option value="all">All</option>
                  {phaseOptions.map((row) => (
                    <option key={`phase-${row.value}`} value={row.value || "Not specified"}>
                      {formatPhaseLabel(row.value)}
                      {Number.isFinite(row.count) ? ` (${row.count})` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="pharma-field">
                <label>Source</label>
                <select
                  value={explorerFilters.source}
                  onChange={(event) => handleExplorerFilterChange("source", event.target.value)}
                >
                  <option value="ctgov">ClinicalTrials.gov</option>
                  <option value="all">All</option>
                </select>
              </div>
              <div className="pharma-field">
                <label>Country</label>
                <select
                  value={explorerFilters.country}
                  onChange={(event) => handleExplorerFilterChange("country", event.target.value)}
                >
                  <option value="">All countries</option>
                  {countryOptions.map((row) => (
                    row.value ? (
                      <option key={`match-country-${row.value}`} value={row.value}>
                        {row.value}{Number.isFinite(row.count) ? ` (${row.count})` : ""}
                      </option>
                    ) : null
                  ))}
                </select>
              </div>
              <div className="pharma-field">
                <label>Sponsor contains</label>
                <input
                  value={explorerFilters.sponsor}
                  onChange={(event) => handleExplorerFilterChange("sponsor", event.target.value)}
                  placeholder="company, hospital..."
                />
              </div>
            </div>
            <div className="pharma-row-actions">
              <button type="button" className="pharma-btn secondary" onClick={applyExplorerFilters}>
                Refresh Trial Set
              </button>
              <button type="button" className="pharma-btn secondary" onClick={resetExplorerFilters}>
                Clear Filters
              </button>
              <span className="pharma-muted">
                {trialsPayload.totalCount} trials in scope
              </span>
            </div>
            {hasCatalogFilters ? (
              <div className="pharma-filter-token-row">
                {activeTrialFilterTokens.map((token) => (
                  <span key={token} className="pharma-chip active">{token}</span>
                ))}
              </div>
            ) : null}
            <div className="pharma-matching-scope-grid">
              <div className="pharma-kv-grid">
                <div><span>Trials in scope</span><strong>{trialsPayload.totalCount}</strong></div>
                <div><span>Recruiting</span><strong>{recruitingOverviewCount}</strong></div>
                <div><span>Active filters</span><strong>{activeTrialFilterTokens.length}</strong></div>
                <div><span>Last catalog update</span><strong>{formatDateTime(catalogLastUpdated)}</strong></div>
              </div>
              <div className="pharma-overview-grid pharma-overview-grid-compact">
                <OverviewMetricChart
                  title="Status mix"
                  rows={statusOverviewRows}
                  formatLabel={(value) => formatStatusLabel(value)}
                />
                <OverviewMetricChart
                  title="Phase mix"
                  rows={phaseOverviewRows}
                  formatLabel={(value) => formatPhaseLabel(value)}
                />
              </div>
            </div>
          </section>

          <section className="pharma-card pharma-matching-process-card">
            <div className="pharma-step-label"><span className="pharma-step-num">3</span> Matching Process</div>
            <div className="pharma-process-actions">
              <div>
                <h3>Start the trial matching run</h3>
                <p className="pharma-muted">
                  Build the patient-specific context, evaluate the filtered trial set criterion by criterion, then rank the matched studies.
                </p>
              </div>
              <button
                type="button"
                className="pharma-btn primary"
                onClick={runMatchingFlow}
                disabled={!patientContextReady || loading || patientContextLoading || randomPatientLoading || trialsPayload.totalCount <= 0}
              >
                {loading ? "Running Matching..." : "Start Clinical Trial Matching"}
              </button>
            </div>
            <div className="pharma-process-preflight">
              <span className={`pharma-evidence-chip ${patientContextReady ? "pass" : "neutral"}`}>
                Patient {patientContextReady ? "loaded" : "pending"}
              </span>
              <span className="pharma-evidence-chip neutral">{trialsPayload.totalCount} trials in scope</span>
              {query ? <span className="pharma-evidence-chip neutral">Intent query ready</span> : null}
              <span className={`pharma-evidence-chip ${patientContextReady ? patientContextPreflightTone : "neutral"}`}>
                {patientContextReady ? patientContextDisplayStatus.label : "Awaiting patient context"}
              </span>
            </div>
            <MatchingPipelineExplainer
              pipelineVisibility={pipelineVisibility}
              active={loading}
              hasRun={matchingResultsVisible}
              autoScrollOnActive
            />
          </section>

          {matchingResultsVisible ? (
            <>
          {/* Ranked Match Cards */}
          <section className="pharma-card" style={{ marginBottom: 10 }}>
            <h2>Ranked Trial Matches ({matchRows.length})</h2>
            <div className="pharma-match-cards">
              {matchRows.map((row) => (
                <article
                  key={row.trialId}
                  className="pharma-match-card"
                  onClick={() => openTrialOverlay(row)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => { if (event.key === "Enter") openTrialOverlay(row); }}
                >
                  <div className="pharma-match-card-head">
                    <span className="trial-id">{row.trialId}</span>
                    <span className="trial-status">{row.status || "-"}</span>
                  </div>
                  <h4>{row.title}</h4>
                  <div className="pharma-match-card-scores">
                    <span className="pharma-match-card-score">
                      Final: <strong>{formatScore(row.finalScore)}</strong>
                    </span>
                    <span className="pharma-match-card-score">
                      Structured: <strong>{formatScore(row.structuredScore)}</strong>
                    </span>
                    <span className={`pharma-assessment-chip ${formatAssessment(row.overallAssessment, row.isEligibleStructured).className}`}>
                      {formatAssessment(row.overallAssessment, row.isEligibleStructured).label}
                    </span>
                    {row.semanticScore != null ? (
                      <span className="pharma-match-card-score">
                        Vector: <strong>{formatScore(row.semanticScore)}</strong>
                        <span className="pharma-vector-tag" style={{ marginLeft: 4 }}>Voyage</span>
                      </span>
                    ) : null}
                    {row.criteriaLevelScore != null ? (
                      <span className="pharma-match-card-score">
                        Criteria: <strong>{formatScore(row.criteriaLevelScore)}</strong>
                      </span>
                    ) : null}
                  </div>
                  {row.prefilterEvaluation ? (
                    <div className="pharma-evidence-chip-row" style={{ marginBottom: 10 }}>
                      <span className={`pharma-evidence-chip ${row.prefilterEvaluation.passesHardFilters ? "pass" : "blocked"}`}>
                        {row.prefilterEvaluation.passesHardFilters ? "Prefilter passed" : "Prefilter blocked"}
                      </span>
                      {Array.isArray(row.prefilterEvaluation.advisoryMatches)
                        ? row.prefilterEvaluation.advisoryMatches.slice(0, 3).map((item) => (
                          <span key={item} className="pharma-evidence-chip neutral">{String(item).replaceAll("_", " ")}</span>
                        ))
                        : null}
                    </div>
                  ) : null}
                  <EligibilityCriteriaChips breakdown={row.criterionBreakdown} />
                  <EligibilityDecisionSummary
                    assessment={row.overallAssessment}
                    decisionSummary={row.decisionSummary}
                    missingCriteria={row.missingCriteria}
                    compact
                  />
                  <MatchReasonHighlights rules={row.ruleEvaluations} maxItemsPerBucket={1} title="Why" />
                  <div className="pharma-match-card-actions">
                    <button type="button" className="pharma-btn secondary">View Details</button>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <BehindTheScenes
            title="Trial Matching & Cohort Pipelines"
            data={[
              { label: "Trial Matching Pipeline", payload: trialMatchPayload?.pipelineVisibility || {} },
              ...(syncSummary ? [{ label: "Last Sync Summary", payload: syncSummary }] : []),
            ]}
          />
            </>
          ) : null}
        </div>
      ) : null}

      {view === "screening" ? (
        <div className="pharma-step-section">
          <div className="pharma-view-header">
            <button type="button" className="pharma-back-link" onClick={() => setView("overview")}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
              Back
            </button>
            <h2>Match Patients To One Clinical Trial</h2>
            <p className="pharma-muted">Filter the catalog, choose one trial, then screen the governed cohort for candidate patients.</p>
          </div>
          <section className="pharma-card" style={{ marginBottom: 14 }}>
            <div className="pharma-step-label"><span className="pharma-step-num">1</span> Filter And Select One Trial</div>
            <div className="pharma-filter-grid">
              <div className="pharma-field">
                <label>Search text</label>
                <input
                  value={explorerFilters.q}
                  onChange={(event) => handleExplorerFilterChange("q", event.target.value)}
                  placeholder="keyword, sponsor, condition..."
                />
              </div>
              <div className="pharma-field">
                <label>Status</label>
                <select
                  value={explorerFilters.status}
                  onChange={(event) => handleExplorerFilterChange("status", event.target.value)}
                >
                  <option value="all">All</option>
                  <option value="RECRUITING">Recruiting</option>
                  <option value="ACTIVE_NOT_RECRUITING">Active, not recruiting</option>
                  <option value="NOT_YET_RECRUITING">Not yet recruiting</option>
                  <option value="ENROLLING_BY_INVITATION">Enrolling by invitation</option>
                  <option value="TERMINATED">Terminated</option>
                </select>
              </div>
              <div className="pharma-field">
                <label>Phase</label>
                <select
                  value={explorerFilters.phase}
                  onChange={(event) => handleExplorerFilterChange("phase", event.target.value)}
                >
                  <option value="all">All</option>
                  {phaseOptions.map((row) => (
                    <option key={`phase-${row.value}`} value={row.value || "Not specified"}>
                      {formatPhaseLabel(row.value)}
                      {Number.isFinite(row.count) ? ` (${row.count})` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="pharma-field">
                <label>Source</label>
                <select
                  value={explorerFilters.source}
                  onChange={(event) => handleExplorerFilterChange("source", event.target.value)}
                >
                  <option value="ctgov">ClinicalTrials.gov</option>
                  <option value="all">All</option>
                </select>
              </div>
              <div className="pharma-field">
                <label>Country</label>
                <select
                  value={explorerFilters.country}
                  onChange={(event) => handleExplorerFilterChange("country", event.target.value)}
                >
                  <option value="">All countries</option>
                  {countryOptions.map((row) => (
                    row.value ? (
                      <option key={`screen-country-${row.value}`} value={row.value}>
                        {row.value}{Number.isFinite(row.count) ? ` (${row.count})` : ""}
                      </option>
                    ) : null
                  ))}
                </select>
              </div>
              <div className="pharma-field">
                <label>Sponsor contains</label>
                <input
                  value={explorerFilters.sponsor}
                  onChange={(event) => handleExplorerFilterChange("sponsor", event.target.value)}
                  placeholder="company, hospital..."
                />
              </div>
            </div>
            <div className="pharma-row-actions">
              <button type="button" className="pharma-btn primary" onClick={applyExplorerFilters}>
                Apply Filters
              </button>
              <button type="button" className="pharma-btn secondary" onClick={resetExplorerFilters}>
                Reset
              </button>
              <span className="pharma-muted">
                {trialsPayload.totalCount} trials &middot; page {explorerPage}/{totalPages}
              </span>
            </div>
            {(trialsPayload.facets?.status || []).length > 0 ? (
              <div className="pharma-facet-wrap mt-6">
                {(trialsPayload.facets?.status || []).slice(0, 8).map((row) => (
                  <button
                    key={`facet-status-${row.value}`}
                    type="button"
                    className={`pharma-chip ${explorerFilters.status === row.value ? "active" : ""}`}
                    onClick={() => handleExplorerFilterChange("status", row.value || "all")}
                  >
                    {row.value || "Not specified"} ({row.count})
                  </button>
                ))}
              </div>
            ) : null}
          </section>

          <section className="pharma-card" style={{ marginBottom: 14 }}>
            <div className="pharma-step-label"><span className="pharma-step-num">2</span> Select A Trial And Screen The Cohort</div>
            <p className="pharma-muted" style={{ marginTop: 4 }}>
              Choose one trial from the list, then open its workspace and launch candidate screening.
            </p>
          </section>

          {showActiveTrialsList ? (
            <>
              <div className="pharma-layout browse-detail">
                <section className="pharma-card pharma-active-trials-panel">
                  <div className="pharma-active-trials-head">
                    <div>
                      <span className="pharma-panel-eyebrow">Candidate trial set</span>
                      <h2>Active Trials</h2>
                      <p className="pharma-active-trials-copy">
                        Filtered breast-cancer studies from the governed ClinicalTrials.gov catalog.
                      </p>
                    </div>
                    <span className="pharma-active-trials-count">{trialsPayload.totalCount}</span>
                  </div>
                  <div className="pharma-trial-list">
                    {(trialsPayload.trials || []).map((trial) => (
                      <article
                        key={trial.trialId}
                        className={`pharma-trial-card ${selectedTrial?.trialId === trial.trialId ? "selected" : ""}`}
                        onClick={() => setSelectedTrial(trial)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            setSelectedTrial(trial);
                          }
                        }}
                      >
                        <div className="pharma-trial-head">
                          <span className="trial-id">{trial.trialId}</span>
                          <span className="trial-status">{trial.status || "-"}</span>
                        </div>
                        <h4>{trial.title}</h4>
                        <p>{trial.summary || "No summary available."}</p>
                        <div className="pharma-trial-meta">
                          <span>{trial.phase || "Not specified"}</span>
                          <span>{trial.sponsor || "-"}</span>
                          <span>{(trial.siteCoverage || []).length} sites</span>
                        </div>
                      </article>
                    ))}
                  </div>
                  <div className="pharma-row-actions">
                    <button type="button" className="pharma-btn secondary" onClick={() => handlePageChange(-1)} disabled={explorerPage <= 1}>
                      Previous
                    </button>
                    <button type="button" className="pharma-btn secondary" onClick={() => handlePageChange(1)} disabled={explorerPage >= totalPages}>
                      Next
                    </button>
                  </div>
                </section>

                <section className="pharma-card pharma-selected-trial-panel">
                  <div className="pharma-selected-trial-head">
                    <div>
                      <span className="pharma-panel-eyebrow">Workspace</span>
                      <h2>Selected Trial</h2>
                      <p className="pharma-muted">Review the selected protocol, then open its dedicated workspace or launch candidate screening.</p>
                    </div>
                    {selectedTrial?.trialId ? (
                      <span className="pharma-selected-trial-badge">{selectedTrial.trialId}</span>
                    ) : null}
                  </div>
                  {selectedTrial ? (
                    <>
                      <div className="pharma-row-actions" style={{ marginBottom: 8 }}>
                        <button
                          type="button"
                          className="pharma-btn primary"
                          onClick={() => openTrialOverlay(selectedTrial)}
                        >
                          Open Trial Workspace
                        </button>
                        <button
                          type="button"
                          className="pharma-btn primary"
                          onClick={() => {
                            openTrialOverlay(selectedTrial);
                            runSelectedTrialCandidateMatch(selectedTrial.trialId);
                          }}
                          disabled={trialCandidateLoading}
                        >
                          {trialCandidateLoading ? "Screening..." : "Find Candidates"}
                        </button>
                        {selectedTrial.sourceUrl ? (
                          <a href={selectedTrial.sourceUrl} target="_blank" rel="noreferrer" className="pharma-btn secondary">
                            Source Link
                          </a>
                        ) : null}
                      </div>
                      <h3 className="mt">Summary</h3>
                      <div className="pharma-overlay-summary">{selectedTrial.summary || "No summary available."}</div>
                      <h3 className="mt">Inclusion & Exclusion Criteria</h3>
                      <EligibilityCriteriaPanel trial={selectedTrial} maxItemsPerSection={6} />
                      <div className="pharma-kv-grid">
                        <div><span>Trial ID</span><strong>{selectedTrial.trialId}</strong></div>
                        <div><span>Status</span><strong>{selectedTrial.status || "-"}</strong></div>
                        <div><span>Phase</span><strong>{selectedTrial.phase || "-"}</strong></div>
                        <div><span>Sponsor</span><strong>{selectedTrial.sponsor || "-"}</strong></div>
                        <div><span>Source</span><strong>{selectedTrial.source || "-"}</strong></div>
                        <div><span>Source Updated</span><strong>{formatDateTime(selectedTrial.sourceStudyLastUpdated)}</strong></div>
                      </div>
                    </>
                  ) : (
                    <p className="pharma-muted">Select one trial from the left to continue to stage 2.</p>
                  )}
                </section>
              </div>

              <BehindTheScenes
                title="Explorer & Source Pipelines"
                data={[
                  { label: "Explorer Query Pipeline", payload: trialsPayload.pipelineVisibility || {} },
                  ...(syncSummary ? [{ label: "ClinicalTrials Sync Response", payload: syncSummary }] : []),
                ]}
              />
            </>
          ) : (
            <section className="pharma-card">
              <p className="pharma-muted">
                Apply filters to load the trial list.
              </p>
            </section>
          )}
        </div>
      ) : null}
      </>
      ) : null}

      {/* ── Trial Detail Overlay ── */}
      {overlayTrial ? (
        <TrialDetailOverlay
          trial={overlayTrial}
          pipelineVisibility={pipelineVisibility}
          patientEvidencePack={trialMatchPayload?.patientEvidencePack || patientContextPayload?.evidencePack}
          onClose={() => setOverlayTrial(null)}
          onFindCandidates={() => runSelectedTrialCandidateMatch(overlayTrial.trialId)}
          onRunLlmAdjudication={runOverlayTrialLlmAdjudication}
          llmAdjudicationLoading={llmAdjudicationLoading}
          llmAdjudicationPayload={llmAdjudicationPayload}
          candidatePayload={trialCandidatePayload}
          candidateLoading={trialCandidateLoading}
          flaggedCandidates={flaggedCandidates}
          onToggleFlag={toggleFlag}
          expandedCandidates={expandedCandidates}
          onToggleExpand={toggleCandidateExpansion}
        />
      ) : null}
    </div>
  );
}
