import React, { useEffect, useMemo, useState } from "react";
import {
  APIGetClinicalTrialCanonicalTree,
  APIGetClinicalTrialsHealth,
  APIGetRandomPharmaPatientContext,
  APIGetProductPatientWorkspace,
  APIListClinicalTrialMatchRuns,
  APIListClinicalTrials,
  APIListProductPatients,
} from "../../../services/APIService";

const safeCall = (fn) => fn().catch(() => null);

const MATCH_DIRECTIONS = [
  {
    key: "patient_to_trials",
    title: "One Patient -> Many Clinical Trials",
    description:
      "Start from one curated patient and evaluate each inclusion and exclusion criterion across the canonical trial catalog.",
  },
  {
    key: "trial_to_patients",
    title: "One Clinical Trial -> Many Patients",
    description:
      "Start from one compiled trial contract and screen the patient cohort with deterministic, criterion-level traces.",
  },
];

function getTrialId(trial) {
  return String(
    trial?._id || trial?.nct_id || trial?.trialId || trial?.nctId || "",
  ).trim();
}

function getTrialTitle(trial) {
  return (
    trial?.brief_title ||
    trial?.title ||
    trial?.briefTitle ||
    getTrialId(trial) ||
    "Untitled clinical trial"
  );
}

function formatInteger(value) {
  return new Intl.NumberFormat("en-US").format(Number(value || 0));
}

function formatDateTime(value) {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
}

function formatTrialStatus(trial) {
  return String(
    trial?.status || trial?.overall_status || trial?.overallStatus || "Unknown",
  ).replace(/_/g, " ");
}

function formatLinkageTone(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "linked") return { label: "Linked", tone: "pass" };
  if (normalized === "conflict") return { label: "Conflict", tone: "blocked" };
  if (normalized === "candidate_only" || normalized === "ambiguous") {
    return { label: "Review", tone: "review" };
  }
  return { label: "Unknown", tone: "neutral" };
}

function resolvePatientRef(contextPayload, evidencePayload, matchPayload) {
  const context = contextPayload || {};
  const evidence =
    evidencePayload?.patientEvidenceProfile || evidencePayload || {};
  const matchProfile = matchPayload?.patientMatchProfile || matchPayload || {};
  return String(
    context?.patientSummary?.patientRef ||
    context?.patientLink?.syntheaPatientRef ||
    context?.patientRef ||
    evidence?.patientRef ||
      evidence?.identity?.syntheaPatientRef ||
      matchProfile?.patientRef ||
      matchProfile?.identity?.syntheaPatientRef ||
      "",
  ).trim();
}

function countTreeSectionNodes(treePayload, sectionName) {
  const roots = Array.isArray(treePayload?.roots) ? treePayload.roots : [];
  const root = roots.find(
    (row) =>
      String(row?.section || "").toLowerCase() ===
      String(sectionName || "").toLowerCase(),
  );
  if (!root) return 0;

  let count = 0;
  const visit = (nodes) => {
    (nodes || []).forEach((node) => {
      if (String(node?.nodeType || "").toLowerCase() !== "section_root") {
        count += 1;
      }
      visit(node?.children || []);
    });
  };
  visit(root?.children || []);
  return count;
}

function formatFeatureValue(value) {
  if (value == null || value === "") return "Unknown";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.length ? value.join(", ") : "Unknown";
  if (typeof value === "object") {
    if (value.text) return value.text;
    if (value.code) return value.code;
    if (value.value != null && value.unit)
      return `${value.value} ${value.unit}`;
    if (value.value != null) return String(value.value);
  }
  return String(value);
}

function resolvedFeatureValue(featurePayload) {
  if (!featurePayload || typeof featurePayload !== "object") return null;
  return (
    featurePayload.resolvedValue ||
    featurePayload.resolved ||
    featurePayload.status ||
    null
  );
}

function extractLatestTrialTimestamp(trials = []) {
  const candidateValues = (trials || [])
    .map(
      (trial) =>
        trial?.updatedAt ||
        trial?.generatedAt ||
        trial?.trialVersion?.sourceStudyLastUpdated ||
        trial?.trialVersion?.fetchedAt ||
        null,
    )
    .filter(Boolean);
  return candidateValues.sort(
    (left, right) => new Date(right).getTime() - new Date(left).getTime(),
  )[0] || null;
}

function HeroStat({ label, value }) {
  return (
    <div className="pharma-header-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function DirectionCard({ title, description, onClick, buttonLabel }) {
  return (
    <div className="pharma-card">
      <h3>{title}</h3>
      <p className="pharma-muted" style={{ marginTop: 8, lineHeight: 1.6 }}>
        {description}
      </p>
      <div className="pharma-landing-card-actions">
        <button type="button" className="pharma-btn primary" onClick={onClick}>
          {buttonLabel}
        </button>
      </div>
    </div>
  );
}

export default function PharmaLanding({
  navigateSpace,
  selectedPatientRef,
  selectedTrialId,
}) {
  const [serviceHealth, setServiceHealth] = useState(null);
  const [patientCatalogSummary, setPatientCatalogSummary] = useState(null);
  const [storedTrials, setStoredTrials] = useState([]);
  const [trialCatalogTotalCount, setTrialCatalogTotalCount] = useState(0);
  const [trialTree, setTrialTree] = useState(null);
  const [patientContext, setPatientContext] = useState(null);
  const [patientEvidence, setPatientEvidence] = useState(null);
  const [patientMatchProfile, setPatientMatchProfile] = useState(null);
  const [recentRuns, setRecentRuns] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadOverview() {
      setLoading(true);

      const [health, trials, randomPatient, runs, patientCatalog] =
        await Promise.allSettled([
          safeCall(() => APIGetClinicalTrialsHealth()),
          safeCall(() =>
            APIListClinicalTrials({ disease_area: "breast_cancer", limit: 8 }),
          ),
          safeCall(() => APIGetRandomPharmaPatientContext()),
          safeCall(() => APIListClinicalTrialMatchRuns({ limit: 4 })),
          safeCall(() =>
            APIListProductPatients({ focus: "breast_cancer", limit: 1 }),
          ),
        ]);

      if (cancelled) return;

      const healthValue = health.status === "fulfilled" ? health.value : null;
      const trialValue = trials.status === "fulfilled" ? trials.value : null;
      const patientValue =
        randomPatient.status === "fulfilled" ? randomPatient.value : null;
      const runsValue = runs.status === "fulfilled" ? runs.value : null;
      const patientCatalogValue =
        patientCatalog.status === "fulfilled" ? patientCatalog.value : null;

      const nextTrials = Array.isArray(trialValue?.trials)
        ? trialValue.trials
        : Array.isArray(trialValue)
          ? trialValue
          : [];

      setServiceHealth(healthValue || null);
      setStoredTrials(nextTrials);
      setTrialCatalogTotalCount(trialValue?.totalCount || nextTrials.length || 0);
      setPatientContext(patientValue || null);
      setPatientCatalogSummary(patientCatalogValue?.summary || null);
      setRecentRuns(
        Array.isArray(runsValue?.runs)
          ? runsValue.runs
          : Array.isArray(runsValue)
            ? runsValue
            : [],
      );

      const preferredTrialId = selectedTrialId || getTrialId(nextTrials[0]);
      const preferredPatientRef =
        selectedPatientRef
        || patientCatalogValue?.patients?.[0]?.patientRef
        || resolvePatientRef(
          patientValue,
          patientValue?.patientEvidenceProfile,
          patientValue?.patientMatchProfile,
        );

      const [treeValue, patientWorkspaceValue] = await Promise.all([
        preferredTrialId
          ? safeCall(() => APIGetClinicalTrialCanonicalTree(preferredTrialId))
          : Promise.resolve(null),
        preferredPatientRef
          ? safeCall(() => APIGetProductPatientWorkspace(preferredPatientRef))
          : Promise.resolve(null),
      ]);

      if (cancelled) return;

      setTrialTree(
        treeValue?.canonical_tree || treeValue?.tree || treeValue || null,
      );
      setPatientContext(patientWorkspaceValue || patientValue || null);
      setPatientEvidence(
        patientWorkspaceValue?.patientEvidenceProfile
          || patientValue?.patientEvidenceProfile
          || null,
      );
      setPatientMatchProfile(
        patientWorkspaceValue?.patientMatchProfile
          || patientValue?.patientMatchProfile
          || null,
      );
      setLoading(false);
    }

    loadOverview();
    return () => {
      cancelled = true;
    };
  }, [selectedPatientRef, selectedTrialId]);

  const highlightedTrial = useMemo(() => {
    if (selectedTrialId) {
      return (
        storedTrials.find((trial) => getTrialId(trial) === selectedTrialId) ||
        storedTrials[0] ||
        null
      );
    }
    return storedTrials[0] || null;
  }, [selectedTrialId, storedTrials]);

  const highlightedTrialId = getTrialId(highlightedTrial);
  const resolvedPatientRef = useMemo(
    () =>
      selectedPatientRef ||
      resolvePatientRef(patientContext, patientEvidence, patientMatchProfile),
    [patientContext, patientEvidence, patientMatchProfile, selectedPatientRef],
  );
  const latestCorpusUpdatedAt = useMemo(
    () => extractLatestTrialTimestamp(storedTrials),
    [storedTrials],
  );

  const resolvedFeatures = patientEvidence?.resolvedFeatures || {};
  const patientName =
    patientContext?.patientSummary?.displayName ||
    patientContext?.patientContext?.patientName ||
    patientEvidence?.demographics?.name ||
    patientEvidence?.identity?.canonicalPatientKey ||
    resolvedPatientRef ||
    "Patient preview";
  const patientLinkage = formatLinkageTone(
    patientMatchProfile?.identity?.linkageStatus ||
    patientEvidence?.identity?.linkageStatus ||
      patientContext?.patientSummary?.linkageStatus ||
      patientContext?.patientLink?.linkageStatus ||
      patientContext?.patientLink?.status,
  );
  const inclusionCount = countTreeSectionNodes(trialTree, "inclusion");
  const exclusionCount = countTreeSectionNodes(trialTree, "exclusion");
  const latestRun = recentRuns[0] || null;
  const corpusCount = trialCatalogTotalCount || storedTrials.length;
  const runtimeReady =
    patientMatchProfile?.runtimeReadiness?.runnable
    || patientMatchProfile?.runtimeReadiness?.matchReady
    || false;

  return (
    <div className="pharma-page">
      <div className="pharma-header">
        <div className="pharma-header-left">
          <h1>
            One workspace for trial ingestion, patient data, and bidirectional
            matching.
          </h1>
          <p>
            This overview stays inside the same Pharma Navigator flow. Start by
            compiling clinical trials, observe the patient evidence we really
            have, then run matching in either direction without switching into a
            second product shell.
          </p>
        </div>
        <div className="pharma-header-stats">
          <HeroStat
            label="Trials in Corpus"
            value={formatInteger(corpusCount)}
          />
          <HeroStat
            label="Patients Ready"
            value={formatInteger(patientCatalogSummary?.runnableCount || 0)}
          />
          <HeroStat
            label="Recent Runs"
            value={formatInteger(recentRuns.length)}
          />
        </div>
      </div>

      <div className="pharma-actions-hero">
        <section className="pharma-action-hero-card pharma-landing-hero-card">
          <span className="pharma-action-hero-icon">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#00684A"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M4 4h16v4H4z" />
              <path d="M7 8v12" />
              <path d="M17 8v12" />
              <path d="M9 12h6" />
              <path d="M9 16h6" />
              <path d="M9 20h6" />
            </svg>
          </span>
          <div className="pharma-action-hero-body">
            <span className="pharma-action-hero-kicker">
              1. Clinical trial ingestion
            </span>
            <strong>Compile canonical clinical trial contracts</strong>
            <span>
              Search ClinicalTrials.gov, ingest studies, and structure inclusion
              and exclusion criteria for deterministic evaluation.
            </span>
            <div className="pharma-landing-card-actions">
              <button
                type="button"
                className="pharma-btn primary"
                onClick={() =>
                  navigateSpace("ingestion", {
                    trialId: highlightedTrialId,
                    launchSource: "navigator_overview",
                  })
                }
              >
                Continue in Ingestion
              </button>
            </div>
          </div>
        </section>

        <section className="pharma-action-hero-card pharma-landing-hero-card">
          <span className="pharma-action-hero-icon">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#00684A"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
          </span>
          <div className="pharma-action-hero-body">
            <span className="pharma-action-hero-kicker">2. Patient data</span>
            <strong>Observe the evidence we have around the patient</strong>
            <span>
              Inspect linkage, disease posture, biomarkers, stage, timeline, and
              gaps before asking the matcher to make any claim.
            </span>
            <div className="pharma-landing-card-actions">
              <button
                type="button"
                className="pharma-btn primary"
                onClick={() =>
                  navigateSpace("patient", {
                    patientRef: resolvedPatientRef,
                    launchSource: "navigator_overview",
                  })
                }
              >
                Continue in Patient Data
              </button>
            </div>
          </div>
        </section>

        <section className="pharma-action-hero-card pharma-landing-hero-card">
          <span className="pharma-action-hero-icon">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#00684A"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </span>
          <div className="pharma-action-hero-body">
            <span className="pharma-action-hero-kicker">3. Matching</span>
            <strong>Run deterministic matching in both directions</strong>
            <span>
              Choose whether you want one patient against many trials or one
              trial against many patients, then inspect criterion-level traces.
            </span>
            <div className="pharma-landing-card-actions pharma-landing-card-actions--split">
              <button
                type="button"
                className="pharma-btn primary"
                onClick={() =>
                  navigateSpace("matching", {
                    direction: "patient_to_trials",
                    patientRef: resolvedPatientRef,
                    trialId: highlightedTrialId,
                    launchSource: "navigator_overview",
                  })
                }
              >
                Patient to Trials
              </button>
              <button
                type="button"
                className="pharma-btn secondary"
                onClick={() =>
                  navigateSpace("matching", {
                    direction: "trial_to_patients",
                    patientRef: resolvedPatientRef,
                    trialId: highlightedTrialId,
                    launchSource: "navigator_overview",
                  })
                }
              >
                Trial to Patients
              </button>
            </div>
          </div>
        </section>
      </div>

      <div className="pharma-overview-grid">
        <section className="pharma-card">
          <h2>Clinical Trial Ingestion Snapshot</h2>
          <p className="pharma-muted">
            What is currently in the trial corpus and how the selected trial has
            already been structured for matching.
          </p>
          <div className="pharma-kv-grid">
            <div>
              <span>Service</span>
              <strong>
                {serviceHealth?.ok
                  ? "Healthy"
                  : loading
                    ? "Checking"
                    : "Unavailable"}
              </strong>
            </div>
            <div>
              <span>Selected Trial</span>
              <strong>{highlightedTrialId || "Awaiting trial"}</strong>
            </div>
            <div>
              <span>Trial Status</span>
              <strong>{formatTrialStatus(highlightedTrial)}</strong>
            </div>
            <div>
              <span>Inclusion Criteria</span>
              <strong>{formatInteger(inclusionCount)}</strong>
            </div>
            <div>
              <span>Exclusion Criteria</span>
              <strong>{formatInteger(exclusionCount)}</strong>
            </div>
            <div>
              <span>Last Source Refresh</span>
              <strong>
                {formatDateTime(latestCorpusUpdatedAt)}
              </strong>
            </div>
          </div>
          <div className="pharma-landing-note">
            <strong>{getTrialTitle(highlightedTrial)}</strong>
            <span>
              The ingestion workspace should be the place where we can see the
              trial come in from ClinicalTrials.gov and confirm the canonical
              inclusion/exclusion structure before matching starts.
            </span>
          </div>
        </section>

        <section className="pharma-card">
          <h2>Patient Data Snapshot</h2>
          <p className="pharma-muted">
            What evidence is currently available for the selected patient and
            how ready it is for deterministic matching.
          </p>
          <div className="pharma-kv-grid">
            <div>
              <span>Patient</span>
              <strong>{patientName}</strong>
            </div>
            <div>
              <span>Patient Ref</span>
              <strong>{resolvedPatientRef || "Awaiting patient"}</strong>
            </div>
            <div>
              <span>Linkage</span>
              <strong>{patientLinkage.label}</strong>
            </div>
            <div>
              <span>Primary Cancer</span>
              <strong>
                {formatFeatureValue(
                  resolvedFeatureValue(resolvedFeatures?.primaryCancer),
                )}
              </strong>
            </div>
            <div>
              <span>Stage / Extent</span>
              <strong>
                {formatFeatureValue(
                  resolvedFeatureValue(resolvedFeatures?.stageExtent) ||
                    resolvedFeatureValue(resolvedFeatures?.stageGroup),
                )}
              </strong>
            </div>
            <div>
              <span>HER2</span>
              <strong>
                {formatFeatureValue(
                  resolvedFeatureValue(resolvedFeatures?.HER2) ||
                    resolvedFeatureValue(resolvedFeatures?.her2Status),
                )}
              </strong>
            </div>
            <div>
              <span>ECOG</span>
              <strong>
                {formatFeatureValue(resolvedFeatureValue(resolvedFeatures?.ECOG))}
              </strong>
            </div>
            <div>
              <span>Runtime Readiness</span>
              <strong>
                {runtimeReady
                  ? "Match-ready"
                  : "Needs review"}
              </strong>
            </div>
          </div>
          <div className="pharma-evidence-chip-row">
            <span className={`pharma-evidence-chip ${patientLinkage.tone}`}>
              {patientLinkage.label}
            </span>
            <span
              className={`pharma-evidence-chip ${runtimeReady ? "pass" : "review"}`}
            >
              {runtimeReady
                ? "Ready for matching"
                : "Review patient evidence"}
            </span>
          </div>
        </section>
      </div>

      <div className="pharma-overview-grid">
        <section className="pharma-card">
          <h2>Clinical Trial Matching</h2>
          <p className="pharma-muted">
            Use the same workbench to move in either direction, with one
            selected trial and one selected patient carried as shared context.
          </p>
          <div className="pharma-overview-grid-compact">
            {MATCH_DIRECTIONS.map((direction) => (
              <DirectionCard
                key={direction.key}
                title={direction.title}
                description={direction.description}
                buttonLabel={
                  direction.key === "patient_to_trials"
                    ? "Run Patient to Trials"
                    : "Run Trial to Patients"
                }
                onClick={() =>
                  navigateSpace("matching", {
                    direction: direction.key,
                    patientRef: resolvedPatientRef,
                    trialId: highlightedTrialId,
                    launchSource: "navigator_overview",
                  })
                }
              />
            ))}
          </div>
        </section>

        <section className="pharma-card">
          <h2>Working Set</h2>
          <p className="pharma-muted">
            The overview keeps one trial and one patient in focus so the next
            step feels like continuing the same workflow rather than launching a
            different system.
          </p>
          <div className="pharma-kv-grid">
            <div>
              <span>Trial in Focus</span>
              <strong>{highlightedTrialId || "Select one trial"}</strong>
            </div>
            <div>
              <span>Patient in Focus</span>
              <strong>{resolvedPatientRef || "Select one patient"}</strong>
            </div>
            <div>
              <span>Latest Match Direction</span>
              <strong>
                {String(latestRun?.direction || "patient_to_trials").replace(
                  /_/g,
                  " ",
                )}
              </strong>
            </div>
            <div>
              <span>Latest Run Time</span>
              <strong>{formatDateTime(latestRun?.startedAt)}</strong>
            </div>
          </div>
          <div className="pharma-landing-note">
            <strong>How this should feel</strong>
            <span>
              Ingestion prepares the trial. Patient Data shows the evidence we
              have. Matching executes in either direction. The user should never
              feel like one product is handing them off to another.
            </span>
          </div>
        </section>
      </div>
    </div>
  );
}
