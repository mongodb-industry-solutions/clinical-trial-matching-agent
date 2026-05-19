import React, { useEffect, useMemo, useState } from "react";

const yesNo = (value) => (value ? "Yes" : "No");
const formatPct = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}%` : "-";
};
const humanize = (value) => String(value || "").replaceAll("_", " ").trim() || "-";
const formatRunDate = (value) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
};

const PROCESS_CHAIN = [
  { id: "catalog", label: "Trial catalog", detail: "ClinicalTrials.gov studies normalized into canonical trial contracts" },
  { id: "product", label: "Semantic product", detail: "Patient-to-trials or trial-to-patients flow chooses the governed contract" },
  { id: "prefilter", label: "Deterministic prefilter", detail: "Safe hard filters reduce the search space before retrieval" },
  { id: "retrieve", label: "Retrieval", detail: "Atlas Search or vectors help recall, but do not decide eligibility" },
  { id: "adjudicate", label: "Criterion adjudication", detail: "Every inclusion and exclusion criterion is evaluated independently" },
  { id: "score", label: "Scoring", detail: "Final ranking blends structured evidence, completeness, and retrieval signals" },
  { id: "route", label: "Route + trace", detail: "Navigator, control plane, and pattern surfaces expose the runtime decision" },
];

const SEMANTIC_PRODUCTS = [
  {
    id: "catalog_overview",
    name: "Catalog Overview",
    badge: "overview",
    purpose: "Summarize the current breast-cancer trial universe and show refresh posture before any patient is bound.",
    requires: "No patient context",
    runsVia: "Trial list facets + source status + sync metadata",
    answersAs: "Overview scorecards and charts",
    collections: [
      "demo_hc_virtual_hospital.pharma_clinical_trials",
    ],
  },
  {
    id: "patient_to_trials",
    name: "Match Clinical Trials For One Patient",
    badge: "patient-to-trials",
    purpose: "Select one screening patient, scope the catalog, and rank trials with criterion-level evidence.",
    requires: "Appointment context + optional patient id + scoped trial filters",
    runsVia: "Leafy context -> patient facts -> trial prefilter -> retrieval -> criterion adjudication",
    answersAs: "Ranked trial list with decision trace",
    collections: [
      "demo_hc_virtual_hospital.enriched_appointments",
      "demo_hc_virtual_hospital.clinicalreports",
      "demo_hc_virtual_hospital.pharma_patient_facts",
      "demo_hc_virtual_hospital.pharma_clinical_trials",
    ],
  },
  {
    id: "trial_to_patients",
    name: "Match Patients To One Clinical Trial",
    badge: "trial-to-patients",
    purpose: "Filter the trial catalog, choose one study, and screen the unified patient registry candidate by candidate.",
    requires: "Selected trial contract",
    runsVia: "Canonical trial contract -> profile prefilter -> longitudinal retrieval -> criterion adjudication",
    answersAs: "Cohort candidate list with row-by-row evidence",
    collections: [
      "hc_fhir_synthea_r4_sep2019_canonical.fhir_resources",
      "demo_hc_virtual_hospital.pharma_patient_profiles",
      "demo_hc_virtual_hospital.pharma_patient_facts",
      "demo_hc_virtual_hospital.pharma_clinical_trials",
    ],
  },
  {
    id: "criterion_trace",
    name: "Criterion Evidence Trace",
    badge: "criterion-trace",
    purpose: "Expose extracted patient data points, assertion type, provenance, and missing evidence for each trial rule.",
    requires: "A bound patient or candidate row",
    runsVia: "Rule evaluation table + deterministic evidence panel",
    answersAs: "Pass / fail / unknown assertions with evidence chips",
    collections: [
      "demo_hc_virtual_hospital.pharma_patient_facts",
      "hc_fhir_synthea_r4_sep2019_canonical.fhir_resources",
    ],
  },
  {
    id: "control_plane",
    name: "Clinical Trial Matching Control Plane",
    badge: "control-plane",
    purpose: "Track data readiness, scoring policy, canonical coverage, sparse oncology domains, linkage health, and backlog.",
    requires: "Runtime summary only",
    runsVia: "Derived control-plane summary",
    answersAs: "Runtime and architecture scorecards",
    collections: [
      "demo_hc_virtual_hospital.pharma_clinical_trials",
      "demo_hc_virtual_hospital.pharma_patient_facts",
      "demo_hc_virtual_hospital.pharma_patient_profiles",
      "demo_hc_virtual_hospital.clinicalreports",
    ],
  },
];

const DATA_ARCHITECTURE = [
  {
    title: "Operational truth",
    tone: "pass",
    items: [
      "hc_fhir_synthea_r4_sep2019_canonical.fhir_resources",
      "demo_hc_virtual_hospital.enriched_appointments",
      "demo_hc_virtual_hospital.clinicalreports",
      "demo_hc_virtual_hospital.pharma_clinical_trials",
    ],
  },
  {
    title: "Deterministic contract",
    tone: "neutral",
    items: [
      "demo_hc_virtual_hospital.pharma_patient_facts",
      "trialDefinitionCanonical",
      "trialPrefilterContract",
    ],
  },
  {
    title: "Semantic accelerators",
    tone: "review",
    items: [
      "demo_hc_virtual_hospital.pharma_patient_profiles",
      "vectorEmbedding",
      "criteriaChunkEmbeddings",
    ],
  },
];

const TRIAL_CONTRACT_STANDARDIZATION = [
  {
    title: "Primary storage model",
    tone: "neutral",
    detail: "Trials are stored as canonical MongoDB documents with a governed trialDefinitionCanonical block, not as raw ODM 2.0 JSON payloads.",
    chips: ["canonical MongoDB contract", "versioned criterion schema", "criterion-level storage"],
  },
  {
    title: "CDISC alignment",
    tone: "pass",
    detail: "The current runtime is USDM aligned at the study-definition level, ODM inspired for criterion identity, SDTM TI inspired for criterion tabulation, and IE compatible for patient outcomes.",
    chips: ["USDM aligned", "ODM inspired", "SDTM TI inspired", "IE compatible"],
  },
  {
    title: "Why the transformation matters",
    tone: "review",
    detail: "Canonical criteria gain stable IDs, typed categories, computability states, hard-filter extraction, and ontology bindings before matching begins.",
    chips: ["stable criterion IDs", "typed categories", "computability flags", "safe prefilters", "explicit gaps"],
  },
];

const SCORING_POLICIES = [
  {
    id: "patient_to_trials",
    title: "Patient -> Trial ranking",
    formula: "Structured eligibility is primary. Vector and retrieval weights are gated by evidence completeness.",
    chips: [
      "structured 0.70-1.00",
      "vector up to 0.20",
      "retrieval up to 0.10",
      "completeness penalty",
      "review cap",
    ],
  },
  {
    id: "trial_to_patients",
    title: "Trial -> Patient cohort screening",
    formula: "Deterministic hard filters first, then 0.75 vector + 0.15 keyword + 0.10 activity retrieval, then 0.55 structured + 0.35 retrieval + 0.10 criteria semantic.",
    chips: [
      "hard filters first",
      "0.75 vector",
      "0.15 keyword",
      "0.10 activity",
      "0.55 structured final",
    ],
  },
];

export default function ProcessEvidenceBoard({ summary, onRunEvaluation, runEvaluationLoading = false }) {
  const [activeTab, setActiveTab] = useState("runtime");
  const [selectedProductId, setSelectedProductId] = useState("patient_to_trials");
  const [selectedRunId, setSelectedRunId] = useState("");

  const runtimePosture = summary?.runtimePosture || {};
  const datasetReadiness = summary?.datasetReadiness || {};
  const linkage = summary?.linkage || {};
  const trialContracts = summary?.trialContracts || {};
  const oncologyCohortQuality = summary?.oncologyCohortQuality || {};
  const sparseOncologyDomains = summary?.sparseOncologyDomains || {};
  const recentEvaluationRuns = useMemo(
    () => (Array.isArray(summary?.recentEvaluationRuns) ? summary.recentEvaluationRuns : []),
    [summary?.recentEvaluationRuns]
  );
  const recentImprovementRuns = useMemo(
    () => (Array.isArray(summary?.recentImprovementRuns) ? summary.recentImprovementRuns : []),
    [summary?.recentImprovementRuns]
  );
  const sparseDomainRows = Array.isArray(sparseOncologyDomains.domains) ? sparseOncologyDomains.domains : [];
  const prefilterCoverage = trialContracts.prefilterCoverage || {};
  const backlog = Array.isArray(summary?.priorityBacklog) ? summary.priorityBacklog : [];
  const readinessChecks = Array.isArray(datasetReadiness.checks) ? datasetReadiness.checks : [];
  const failedChecks = readinessChecks.filter((check) => !check?.passed);
  const runtimeCounts = datasetReadiness.runtimeCounts || {};
  const ready = Boolean(datasetReadiness.ready);
  const selectedProduct = SEMANTIC_PRODUCTS.find((item) => item.id === selectedProductId) || SEMANTIC_PRODUCTS[0];
  const selectedRun = recentEvaluationRuns.find((item) => item?.runId === selectedRunId) || recentEvaluationRuns[0] || null;
  const selectedImprovement = useMemo(() => {
    if (!selectedRun) return recentImprovementRuns[0] || null;
    return recentImprovementRuns.find((item) => item?.evaluationRunId === selectedRun.runId) || null;
  }, [recentImprovementRuns, selectedRun]);
  const parserWarnings = useMemo(
    () => Object.entries(trialContracts.parserWarningCounts || {}).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0)),
    [trialContracts.parserWarningCounts]
  );

  useEffect(() => {
    if (!recentEvaluationRuns.length) {
      setSelectedRunId("");
      return;
    }
    if (!selectedRunId || !recentEvaluationRuns.some((item) => item?.runId === selectedRunId)) {
      setSelectedRunId(recentEvaluationRuns[0]?.runId || "");
    }
  }, [recentEvaluationRuns, selectedRunId]);

  const topMetrics = [
    { label: "Catalog size", value: trialContracts.trialCount ?? 0, tone: "neutral" },
    { label: "Canonical contracts", value: formatPct(trialContracts.canonicalDefinitionPct), tone: "pass" },
    { label: "Deterministic rules", value: formatPct(trialContracts.deterministicRulePct), tone: "pass" },
    { label: "Authoritative linkage", value: formatPct(linkage.authoritativeLinkedPct), tone: "pass" },
    { label: "Sparse domains deterministic", value: formatPct(sparseOncologyDomains.deterministicDomainPct), tone: "review" },
    { label: "Dataset ready", value: yesNo(datasetReadiness.ready), tone: ready ? "pass" : "review" },
  ];

  if (!summary || typeof summary !== "object") return null;

  return (
    <section className="pharma-card pharma-process-board">
      <div className="pharma-process-board-head">
        <div>
          <h2>Clinical Trial Matching Control Plane</h2>
          <p className="pharma-muted">
            See how the trial-matching product runs, how quality is scored over time, and which semantic products and collections power each governed step.
          </p>
        </div>
        <div className="pharma-process-actions">
          <div className="pharma-evidence-chip-row">
            <span className="pharma-evidence-chip neutral">{summary.controlPlaneVersion || "-"}</span>
            <span className={`pharma-evidence-chip ${runtimePosture.heuristicAutoLinkingEnabled ? "blocked" : "pass"}`}>
              heuristics {runtimePosture.heuristicAutoLinkingEnabled ? "enabled" : "disabled"}
            </span>
            <span className={`pharma-evidence-chip ${runtimePosture.retrievalAffectsEligibility ? "blocked" : "pass"}`}>
              retrieval {runtimePosture.retrievalAffectsEligibility ? "decides eligibility" : "does not decide eligibility"}
            </span>
          </div>
          {onRunEvaluation ? (
            <div className="pharma-process-action-row">
              <button
                type="button"
                className="pharma-btn primary"
                onClick={() => onRunEvaluation("runtime_snapshot")}
                disabled={runEvaluationLoading}
              >
                {runEvaluationLoading ? "Running evaluation..." : "Run evaluation"}
              </button>
              <button
                type="button"
                className="pharma-btn secondary"
                onClick={() => onRunEvaluation("full_audit")}
                disabled={runEvaluationLoading}
              >
                Run full audit
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <div className="pharma-process-top-metrics">
        {topMetrics.map((metric) => (
          <div key={metric.label} className={`pharma-process-top-metric ${metric.tone || "neutral"}`}>
            <span>{metric.label}</span>
            <strong>{metric.value}</strong>
          </div>
        ))}
      </div>

      <div className="pharma-process-tabs">
        <button
          type="button"
          className={`pharma-process-tab ${activeTab === "runtime" ? "active" : ""}`}
          onClick={() => setActiveTab("runtime")}
        >
          Runtime
        </button>
        <button
          type="button"
          className={`pharma-process-tab ${activeTab === "architecture" ? "active" : ""}`}
          onClick={() => setActiveTab("architecture")}
        >
          Architecture
        </button>
      </div>

      {activeTab === "runtime" ? (
        <>
          <div className="pharma-process-chain">
            {PROCESS_CHAIN.map((step) => (
              <div key={step.id} className="pharma-process-chain-step">
                <div className="pharma-process-chain-node">{step.label}</div>
                <span>{step.detail}</span>
              </div>
            ))}
          </div>

          <div className={`pharma-process-lead ${ready ? "pass" : "review"}`}>
            <div>
              <strong>{ready ? "Deterministic screening is operational." : "Control plane is visible, but blockers remain."}</strong>
              <p>
                {ready
                  ? "The catalog, patient facts, linkage posture, and deterministic contracts are aligned enough to support governed screening."
                  : "The runtime is surfacing gaps explicitly instead of hiding uncertainty in heuristic ranking."}
              </p>
            </div>
            <div className="pharma-evidence-chip-row">
              <span className={`pharma-evidence-chip ${ready ? "pass" : "review"}`}>dataset ready {yesNo(datasetReadiness.ready)}</span>
              <span className="pharma-evidence-chip neutral">summary mode {humanize(datasetReadiness.summaryMode || "runtime")}</span>
              <span className="pharma-evidence-chip neutral">linkage {humanize(linkage.summaryMode || "runtime")}</span>
            </div>
          </div>

          <div className="pharma-process-panel">
            <h3>Evaluation Runs</h3>
            {recentEvaluationRuns.length > 0 && selectedRun ? (
              <div className="pharma-process-product-shell">
                <div className="pharma-process-product-list">
                  {recentEvaluationRuns.map((run) => {
                    const runOverview = run?.overview || {};
                    return (
                      <button
                        key={run?.runId}
                        type="button"
                        className={`pharma-process-product-btn ${selectedRun?.runId === run?.runId ? "active" : ""}`}
                        onClick={() => setSelectedRunId(run?.runId || "")}
                      >
                        <strong>{formatRunDate(run?.createdAt)}</strong>
                        <span>{humanize(run?.mode)} · {runOverview?.overallScore ?? "-"} score</span>
                      </button>
                    );
                  })}
                </div>

                <div className="pharma-process-product-detail">
                  <div className="pharma-process-product-detail-head">
                    <span className="pharma-pattern-kicker">Evaluation run</span>
                    <h3>{selectedRun.runId}</h3>
                    <p>{formatRunDate(selectedRun.createdAt)} · {humanize(selectedRun.mode)}</p>
                  </div>
                  <div className="pharma-mini-kv-grid">
                    <div><span>Overall score</span><strong>{selectedRun?.overview?.overallScore ?? "-"}</strong></div>
                    <div><span>Supported</span><strong>{selectedRun?.overview?.supportedCount ?? 0}</strong></div>
                    <div><span>Partial</span><strong>{selectedRun?.overview?.partialCount ?? 0}</strong></div>
                    <div><span>Gap</span><strong>{selectedRun?.overview?.gapCount ?? 0}</strong></div>
                  </div>
                  <div className="pharma-evidence-chip-row">
                    <span className={`pharma-evidence-chip ${selectedRun?.overview?.status === "supported" ? "pass" : selectedRun?.overview?.status === "gap" ? "blocked" : "review"}`}>
                      {humanize(selectedRun?.overview?.status)}
                    </span>
                    <span className="pharma-evidence-chip neutral">mode {humanize(selectedRun?.overview?.mode)}</span>
                    <span className={`pharma-evidence-chip ${(selectedRun?.overview?.scoreDelta || 0) >= 0 ? "pass" : "review"}`}>
                      delta {selectedRun?.overview?.scoreDelta ?? "-"}
                    </span>
                  </div>

                  <div className="pharma-process-score-grid">
                    {(selectedRun?.dimensions || []).map((dimension) => (
                      <div key={dimension?.id} className="pharma-process-score-card">
                        <strong>{dimension?.label || "-"}</strong>
                        <p>{dimension?.summary || "-"}</p>
                        <div className="pharma-evidence-chip-row">
                          <span className={`pharma-evidence-chip ${dimension?.status === "supported" ? "pass" : dimension?.status === "gap" ? "blocked" : "review"}`}>
                            {humanize(dimension?.status)}
                          </span>
                          <span className="pharma-evidence-chip neutral">score {dimension?.score ?? "-"}</span>
                          <span className="pharma-evidence-chip neutral">weight {dimension?.weight ?? "-"}</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {selectedImprovement ? (
                    <div className="pharma-process-improvement-card">
                      <div className="pharma-process-domain-head">
                        <strong>Improvement drift</strong>
                        <span className="pharma-evidence-chip neutral">
                          delta {selectedImprovement?.overview?.scoreDelta ?? "-"}
                        </span>
                      </div>
                      <div className="pharma-evidence-chip-row">
                        <span className="pharma-evidence-chip pass">improved {selectedImprovement?.overview?.improvedCount ?? 0}</span>
                        <span className="pharma-evidence-chip review">regressed {selectedImprovement?.overview?.regressedCount ?? 0}</span>
                        <span className="pharma-evidence-chip neutral">gaps {selectedImprovement?.overview?.gapCount ?? 0}</span>
                      </div>
                      {Array.isArray(selectedImprovement?.recommendedActions) && selectedImprovement.recommendedActions.length > 0 ? (
                        <ul className="pharma-list">
                          {selectedImprovement.recommendedActions.slice(0, 4).map((item) => (
                            <li key={`${selectedImprovement.improvementRunId}-${item}`}>
                              <strong>Next</strong>
                              <span>{item}</span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            ) : (
              <p className="pharma-muted">
                No persisted evaluation runs yet. Run an evaluation to capture score history, hotspots, and improvement drift.
              </p>
            )}
          </div>

          <div className="pharma-process-board-grid">
            <div className="pharma-process-panel">
              <h3>Dataset & Cohort</h3>
              <div className="pharma-mini-kv-grid">
                <div><span>Patients</span><strong>{datasetReadiness.patientCount ?? "-"}</strong></div>
                <div><span>Candidate cohort</span><strong>{oncologyCohortQuality.candidatePatients ?? 0}</strong></div>
                <div><span>Average coverage</span><strong>{formatPct(oncologyCohortQuality.candidateAverageCoveragePct)}</strong></div>
                <div><span>Profile embeddings</span><strong>{runtimeCounts.patientProfileEmbeddings ?? 0}</strong></div>
              </div>
              {readinessChecks.length > 0 ? (
                <div className="pharma-evidence-chip-row">
                  {readinessChecks.slice(0, 6).map((check) => (
                    <span key={check?.code || check?.label} className={`pharma-evidence-chip ${check?.passed ? "pass" : "review"}`}>
                      {check?.label || check?.code || "check"}
                    </span>
                  ))}
                </div>
              ) : null}
              {oncologyCohortQuality.matchingReadinessTierCounts ? (
                <div className="pharma-evidence-chip-row">
                  {Object.entries(oncologyCohortQuality.matchingReadinessTierCounts).slice(0, 4).map(([key, value]) => (
                    <span key={key} className="pharma-evidence-chip neutral">{humanize(key)} {value}</span>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="pharma-process-panel">
              <h3>Trial Contract Coverage</h3>
              <div className="pharma-mini-kv-grid">
                <div><span>Trials</span><strong>{trialContracts.trialCount ?? 0}</strong></div>
                <div><span>Canonical defs</span><strong>{formatPct(trialContracts.canonicalDefinitionPct)}</strong></div>
                <div><span>Eligibility rules</span><strong>{trialContracts.patientEligibilityRuleCount ?? 0}</strong></div>
                <div><span>LLM candidate rules</span><strong>{trialContracts.llmCandidateRuleCount ?? 0}</strong></div>
              </div>
              <div className="pharma-evidence-chip-row">
                <span className="pharma-evidence-chip neutral">age filters {prefilterCoverage.hardAgeFilters ?? 0}</span>
                <span className="pharma-evidence-chip neutral">sex filters {prefilterCoverage.hardSexFilters ?? 0}</span>
                <span className="pharma-evidence-chip neutral">BI-RADS soft filters {prefilterCoverage.softBiradsFilters ?? 0}</span>
              </div>
              {parserWarnings.length > 0 ? (
                <div className="pharma-evidence-chip-row">
                  {parserWarnings.slice(0, 4).map(([key, value]) => (
                    <span key={key} className="pharma-evidence-chip review">{humanize(key)} {value}</span>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="pharma-process-panel">
              <h3>Linkage & Screening Capture</h3>
              <div className="pharma-mini-kv-grid">
                <div><span>Authoritative linked</span><strong>{linkage.authoritativeLinked ?? 0}</strong></div>
                <div><span>Unresolved</span><strong>{linkage.unresolved ?? 0}</strong></div>
                <div><span>Appointments with ref</span><strong>{linkage.appointmentsWithSyntheaRef ?? "-"}</strong></div>
                <div><span>Reports with ref</span><strong>{linkage.reportsWithSyntheaRef ?? "-"}</strong></div>
              </div>
              {linkage.statusCounts ? (
                <div className="pharma-evidence-chip-row">
                  {Object.entries(linkage.statusCounts).slice(0, 4).map(([key, value]) => (
                    <span key={key} className="pharma-evidence-chip neutral">{humanize(key)} {value}</span>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="pharma-process-panel">
              <h3>Scoring Policy</h3>
              <div className="pharma-process-score-grid">
                {SCORING_POLICIES.map((policy) => (
                  <div key={policy.id} className="pharma-process-score-card">
                    <strong>{policy.title}</strong>
                    <p>{policy.formula}</p>
                    <div className="pharma-evidence-chip-row">
                      {policy.chips.map((item) => (
                        <span key={`${policy.id}-${item}`} className="pharma-evidence-chip neutral">{item}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="pharma-process-panel">
              <h3>Sparse Oncology Domains</h3>
              <div className="pharma-process-domain-list">
                {sparseDomainRows.slice(0, 4).map((domain) => (
                  <div key={domain.key} className="pharma-process-domain-card">
                    <div className="pharma-process-domain-head">
                      <strong>{domain.label}</strong>
                      <span className={`pharma-evidence-chip ${domain.tone || "neutral"}`}>
                        {humanize(domain.status)}
                      </span>
                    </div>
                    <div className="pharma-evidence-chip-row">
                      <span className="pharma-evidence-chip neutral">facts {domain.patientFactCount ?? 0}</span>
                      <span className="pharma-evidence-chip neutral">Synthea signals {domain.syntheaProfileSignalCount ?? 0}</span>
                      <span className="pharma-evidence-chip neutral">Leafy narratives {domain.leafyNarrativeSignalCount ?? 0}</span>
                    </div>
                    <p className="pharma-process-domain-guidance">{domain.guidance}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="pharma-process-panel">
              <h3>Current Blockers</h3>
              {failedChecks.length > 0 ? (
                <div className="pharma-process-domain-list">
                  {failedChecks.slice(0, 4).map((item) => (
                    <div key={item?.id || item?.label} className="pharma-process-domain-card">
                      <div className="pharma-process-domain-head">
                        <strong>{item?.label || item?.id || "check"}</strong>
                        <span className="pharma-evidence-chip review">failed</span>
                      </div>
                      <p className="pharma-process-domain-guidance">
                        Observed {item?.observed ?? "-"}; expected {item?.expected ?? "-"}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="pharma-muted">No readiness blockers are currently reported.</p>
              )}
            </div>
          </div>

          {backlog.length > 0 ? (
            <div className="pharma-process-backlog">
              <h3>Priority Backlog</h3>
              <ul className="pharma-list">
                {backlog.slice(0, 6).map((item) => (
                  <li key={item}>
                    <strong>Next</strong>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : (
        <div className="pharma-process-architecture">
          <div className="pharma-process-chain">
            {PROCESS_CHAIN.map((step) => (
              <div key={`arch-${step.id}`} className="pharma-process-chain-step">
                <div className="pharma-process-chain-node">{step.label}</div>
                <span>{step.detail}</span>
              </div>
            ))}
          </div>

          <div className="pharma-process-product-shell">
            <div className="pharma-process-product-list">
              {SEMANTIC_PRODUCTS.map((product) => (
                <button
                  key={product.id}
                  type="button"
                  className={`pharma-process-product-btn ${selectedProductId === product.id ? "active" : ""}`}
                  onClick={() => setSelectedProductId(product.id)}
                >
                  <strong>{product.name}</strong>
                  <span>{product.badge}</span>
                </button>
              ))}
            </div>

            <div className="pharma-process-product-detail">
              <div className="pharma-process-product-detail-head">
                <span className="pharma-pattern-kicker">Semantic product</span>
                <h3>{selectedProduct.name}</h3>
                <p>{selectedProduct.purpose}</p>
              </div>
              <div className="pharma-mini-kv-grid">
                <div><span>Requires</span><strong>{selectedProduct.requires}</strong></div>
                <div><span>Runs via</span><strong>{selectedProduct.runsVia}</strong></div>
                <div><span>Answers as</span><strong>{selectedProduct.answersAs}</strong></div>
                <div><span>Collections</span><strong>{selectedProduct.collections.length}</strong></div>
              </div>
              <div className="pharma-evidence-chip-row">
                {selectedProduct.collections.map((item) => (
                  <span key={`${selectedProduct.id}-${item}`} className="pharma-evidence-chip neutral">{item}</span>
                ))}
              </div>
            </div>
          </div>

          <div className="pharma-process-collection-grid">
            {DATA_ARCHITECTURE.map((group) => (
              <div key={group.title} className="pharma-process-collection-card">
                <div className="pharma-process-domain-head">
                  <strong>{group.title}</strong>
                  <span className={`pharma-evidence-chip ${group.tone}`}>{group.items.length} items</span>
                </div>
                <div className="pharma-evidence-chip-row">
                  {group.items.map((item) => (
                    <span key={`${group.title}-${item}`} className="pharma-evidence-chip neutral">{item}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="pharma-process-panel">
            <h3>Trial Contract Standardization</h3>
            <div className="pharma-process-domain-list">
              {TRIAL_CONTRACT_STANDARDIZATION.map((item) => (
                <div key={item.title} className="pharma-process-domain-card">
                  <div className="pharma-process-domain-head">
                    <strong>{item.title}</strong>
                    <span className={`pharma-evidence-chip ${item.tone || "neutral"}`}>{item.chips.length} signals</span>
                  </div>
                  <p className="pharma-process-domain-guidance">{item.detail}</p>
                  <div className="pharma-evidence-chip-row">
                    {item.chips.map((chip) => (
                      <span key={`${item.title}-${chip}`} className="pharma-evidence-chip neutral">{chip}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
