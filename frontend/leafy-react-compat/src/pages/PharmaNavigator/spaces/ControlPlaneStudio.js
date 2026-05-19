import React, { useState, useEffect, useMemo } from "react";
import {
  SpaceHeader, StatusChip, ReadinessScorecard, CriterionStatusBar,
  SectionDivider,
} from "../../../components/PharmaDesignSystem/PharmaDesignSystem";
import {
  APIGetPharmaControlPlane, APIRunPharmaControlPlaneEvaluation,
  APIListClinicalTrialMatchRuns,
  APIGetClinicalTrialFamilyCoverage, APIGetClinicalTrialUnresolvedTop,
  APIGetClinicalTrialMicroBenchmarkLatest, getClinicalTrialMicroBenchmarkReviewSheetUrl,
} from "../../../services/APIService";

/* ---------------------------------------------------------------------------
   Constants & helpers
   --------------------------------------------------------------------------- */

const FAMILY_LABELS = {
  "demographics.age.min": "Age (minimum)",
  "demographics.age.max": "Age (maximum)",
  "demographics.age": "Age",
  "demographics.sex": "Sex",
  "demographics.gender": "Gender",
  "demographics.ecog": "ECOG Performance Status",
  "demographics.karnofsky": "Karnofsky Score",
  "biomarkers.er_status": "ER Status",
  "biomarkers.pr_status": "PR Status",
  "biomarkers.her2_status": "HER2 Status",
  "biomarkers.brca_status": "BRCA Status",
  "biomarkers.pdl1_status": "PD-L1 Status",
  "biomarkers.egfr_status": "EGFR Status",
  "biomarkers.alk_status": "ALK Status",
  "diagnosis.cancer_type": "Cancer Type",
  "diagnosis.stage": "Cancer Stage",
  "diagnosis.histology": "Histology",
  "diagnosis.metastatic": "Metastatic Status",
  "treatment.prior_lines": "Prior Lines of Therapy",
  "treatment.prior_regimens": "Prior Regimens",
  "labs.anc": "Absolute Neutrophil Count",
  "labs.platelets": "Platelet Count",
  "labs.hemoglobin": "Hemoglobin",
  "labs.creatinine": "Creatinine",
  "labs.bilirubin": "Bilirubin",
  "labs.alt": "ALT",
  "labs.ast": "AST",
  "consent.informed_consent": "Informed Consent",
  "consent.contraception": "Contraception Requirements",
};
const TARGET_WINDOW_LABELS = {
  stage_ii_iii_or_locally_advanced: "Stage II/III + Locally Advanced",
  advanced_or_metastatic: "Advanced or Metastatic",
  mixed_or_manual: "Mixed / Manual Stage Scope",
  not_stage_specific: "Not Stage Specific",
  unknown: "Unknown",
};

function humanizeFamilyId(familyId) {
  if (!familyId) return "Unknown Criterion";
  if (FAMILY_LABELS[familyId]) return FAMILY_LABELS[familyId];
  const parts = String(familyId).split(".");
  return parts
    .map((p) => p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()))
    .join(" - ");
}

function humanizeTargetWindow(targetWindow) {
  const normalized = String(targetWindow || "").trim();
  if (!normalized) return "Unknown";
  if (TARGET_WINDOW_LABELS[normalized]) return TARGET_WINDOW_LABELS[normalized];
  return normalized
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function targetWindowStatus(targetWindow) {
  const normalized = String(targetWindow || "").trim();
  if (normalized === "stage_ii_iii_or_locally_advanced") return "pass";
  if (normalized === "advanced_or_metastatic") return "source";
  if (normalized === "mixed_or_manual") return "manual";
  if (normalized === "not_stage_specific") return "neutral";
  return "unknown";
}

const safe = (fn) => fn().catch(() => null);

const EVIDENCE_CATEGORIES = [
  { key: "primaryCancer", label: "Primary Cancer" },
  { key: "erStatus", label: "ER Status" },
  { key: "her2Status", label: "HER2 Status" },
  { key: "stageExtent", label: "Stage / Extent" },
  { key: "treatmentHistory", label: "Treatment History" },
  { key: "labPanel", label: "Lab Panel" },
  { key: "ecog", label: "ECOG" },
];

/* ---------------------------------------------------------------------------
   ControlPlaneStudio
   --------------------------------------------------------------------------- */

export default function ControlPlaneStudio({ navigateSpace }) {
  const [controlPlane, setControlPlane] = useState(null);
  const [matchRuns, setMatchRuns] = useState([]);
  const [familyCoverage, setFamilyCoverage] = useState([]);
  const [unresolvedTop, setUnresolvedTop] = useState([]);
  const [benchmarkBundle, setBenchmarkBundle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [runLoading, setRunLoading] = useState(false);
  const [error, setError] = useState(null);

  /* ---- initial load ---- */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [cp, mr, fc, ut, benchmark] = await Promise.all([
          safe(() => APIGetPharmaControlPlane()),
          safe(() => APIListClinicalTrialMatchRuns({ limit: 5 })),
          safe(() => APIGetClinicalTrialFamilyCoverage()),
          safe(() => APIGetClinicalTrialUnresolvedTop({ limit: 10 })),
          safe(() => APIGetClinicalTrialMicroBenchmarkLatest()),
        ]);
        if (cancelled) return;
        setControlPlane(cp);
        setMatchRuns(Array.isArray(mr?.runs) ? mr.runs : Array.isArray(mr) ? mr : []);
        setFamilyCoverage(
          Array.isArray(fc?.rows) ? fc.rows : Array.isArray(fc?.families) ? fc.families : Array.isArray(fc) ? fc : []
        );
        setUnresolvedTop(
          Array.isArray(ut?.rows) ? ut.rows : Array.isArray(ut?.families) ? ut.families : Array.isArray(ut) ? ut : []
        );
        setBenchmarkBundle(benchmark);
      } catch (err) {
        if (!cancelled) setError(err?.message || "Failed to load control plane data");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  /* ---- run evaluation ---- */
  const handleRunEvaluation = async () => {
    setRunLoading(true);
    try {
      const result = await APIRunPharmaControlPlaneEvaluation();
      setControlPlane(result);
    } catch (err) {
      setError(err?.message || "Evaluation run failed");
    } finally {
      setRunLoading(false);
    }
  };

  /* ---- derived data ---- */
  const summary = useMemo(() => controlPlane?.summary ?? controlPlane ?? {}, [controlPlane]);
  const ev = summary?.evidenceCoverage ?? {};
  const identity = summary?.identity ?? {};
  const backlog = summary?.backlog ?? {};
  const benchmarkReady = summary?.benchmarkReady === true;
  const benchmark = useMemo(() => benchmarkBundle?.benchmark || null, [benchmarkBundle]);
  const benchmarkPack = useMemo(() => benchmark?.pack || {}, [benchmark]);
  const benchmarkQueryProfile = useMemo(
    () => benchmarkPack?.queryProfile || benchmarkPack?.query?.queryProfile || {},
    [benchmarkPack],
  );
  const benchmarkReview = useMemo(() => benchmark?.reviewResults || {}, [benchmark]);
  const benchmarkSmeReview = useMemo(() => benchmark?.smeReviewResults || null, [benchmark]);
  const stageScopeSummary = useMemo(() => benchmarkPack?.stageScopeSummary || {}, [benchmarkPack]);
  const scaleBenchmarkGate = useMemo(() => benchmarkPack?.scaleBenchmarkGate || {}, [benchmarkPack]);
  const recommendedTargetWindow = stageScopeSummary?.recommendedTargetWindow || "";
  const queryTargetWindowGoal = benchmarkQueryProfile?.targetWindowGoal || "";
  const patientStageWindows = useMemo(
    () => (Array.isArray(stageScopeSummary?.patientTargetWindows) ? stageScopeSummary.patientTargetWindows : [])
      .filter((row) => (row?.count ?? 0) > 0),
    [stageScopeSummary],
  );
  const trialStageWindows = useMemo(
    () => (Array.isArray(stageScopeSummary?.trialTargetWindows) ? stageScopeSummary.trialTargetWindows : [])
      .filter((row) => (row?.count ?? 0) > 0),
    [stageScopeSummary],
  );
  const exactOverlapWindows = useMemo(
    () => (Array.isArray(stageScopeSummary?.overlapWindows) ? stageScopeSummary.overlapWindows : [])
      .filter((row) => (row?.patientCount ?? 0) > 0 || (row?.trialCount ?? 0) > 0),
    [stageScopeSummary],
  );
  const effectiveOverlapWindows = useMemo(() => {
    const rows = Array.isArray(stageScopeSummary?.effectiveOverlapWindows)
      ? stageScopeSummary.effectiveOverlapWindows
      : Array.isArray(stageScopeSummary?.overlapWindows)
        ? stageScopeSummary.overlapWindows
        : [];
    return rows.filter((row) => (row?.patientCount ?? 0) > 0 || (row?.trialCount ?? 0) > 0);
  }, [stageScopeSummary]);
  const recommendedTargetWindowSummary = useMemo(() => {
    if (stageScopeSummary?.recommendedTargetWindowSummary) return stageScopeSummary.recommendedTargetWindowSummary;
    return effectiveOverlapWindows.find((row) => row?.targetWindow === recommendedTargetWindow) || null;
  }, [stageScopeSummary, effectiveOverlapWindows, recommendedTargetWindow]);
  const recommendedExactWindowSummary = useMemo(
    () => exactOverlapWindows.find((row) => row?.targetWindow === recommendedTargetWindow) || null,
    [exactOverlapWindows, recommendedTargetWindow],
  );
  const queryTargetWindowGoalSummary = useMemo(
    () => effectiveOverlapWindows.find((row) => row?.targetWindow === queryTargetWindowGoal) || null,
    [effectiveOverlapWindows, queryTargetWindowGoal],
  );
  const demoLaneWindows = useMemo(
    () => effectiveOverlapWindows.filter((row) => row?.targetWindow !== "unknown"),
    [effectiveOverlapWindows],
  );
  const trialScopeIsBroadOnly = useMemo(
    () => trialStageWindows.length > 0 && trialStageWindows.every((row) => row?.targetWindow === "not_stage_specific"),
    [trialStageWindows],
  );
  const stageScopeNarrative = useMemo(() => {
    if (
      queryTargetWindowGoal
      && recommendedTargetWindow
      && queryTargetWindowGoal === recommendedTargetWindow
      && recommendedTargetWindowSummary
    ) {
      return `The planned corpus slice and the best-supported demo lane are both ${humanizeTargetWindow(recommendedTargetWindow)}. This is the cleanest path for a governed two-way matching demonstration.`;
    }
    if (
      queryTargetWindowGoal
      && recommendedTargetWindow
      && queryTargetWindowGoal !== recommendedTargetWindow
      && recommendedTargetWindowSummary
    ) {
      return `The corpus was refreshed to prioritize ${humanizeTargetWindow(queryTargetWindowGoal)}, but the strongest patient-and-trial overlap right now is ${humanizeTargetWindow(recommendedTargetWindow)}. The architecture is working; the gap is patient evidence depth in the planned lane.`;
    }
    if (
      queryTargetWindowGoal
      && queryTargetWindowGoalSummary
      && (queryTargetWindowGoalSummary?.trialCount ?? 0) > 0
      && (queryTargetWindowGoalSummary?.authoritativePatients ?? 0) === 0
    ) {
      return `The frozen pack already contains usable trial contracts for ${humanizeTargetWindow(queryTargetWindowGoal)}, but the authoritative patient cohort in that same lane is still too thin to make it the lead demonstration.`;
    }
    if (recommendedTargetWindow && recommendedTargetWindowSummary) {
      return `The current frozen benchmark can support a ${humanizeTargetWindow(recommendedTargetWindow)} demonstrator with ${recommendedTargetWindowSummary?.authoritativePatients ?? 0} authoritative patients and ${recommendedTargetWindowSummary?.trialCount ?? 0} usable trial contracts in the same governed lane.`;
    }
    if (trialScopeIsBroadOnly) {
      return "Patients are already being organized by disease stage, but the latest frozen trial slice is still broad breast cancer rather than an explicit stage-aligned lane. That is why the control plane cannot recommend one Stage II/III or metastatic window yet.";
    }
    if ((scaleBenchmarkGate?.reasons || []).includes("insufficient_match_ready_patients")) {
      return "The trial side is healthy enough to demonstrate, but the next pass should expand patient evidence coverage so more authoritative patients become fully match-ready.";
    }
    return "Refresh the proof and benchmark artifacts after the next data pass so the overlap between patient posture and trial scope can be frozen and reviewed.";
  }, [
    queryTargetWindowGoal,
    queryTargetWindowGoalSummary,
    recommendedTargetWindow,
    recommendedTargetWindowSummary,
    scaleBenchmarkGate,
    trialScopeIsBroadOnly,
  ]);
  const scaleGateSummary = useMemo(() => {
    const thresholds = scaleBenchmarkGate?.thresholds || {};
    const actual = scaleBenchmarkGate?.actual || {};
    return {
      patientText: `${actual?.matchReadyPatients ?? 0}/${thresholds?.minMatchReadyPatients ?? 0} match-ready patients`,
      trialText: `${actual?.runtimeReadyTrials ?? 0}/${thresholds?.minRuntimeReadyTrials ?? 0} runtime-ready trials`,
    };
  }, [scaleBenchmarkGate]);
  const reviewSheetUrl = getClinicalTrialMicroBenchmarkReviewSheetUrl();
  const reviewedOutcomeMix = useMemo(() => {
    const counts = { candidate: 0, excluded: 0, manual_review_required: 0 };
    ((benchmark?.externalReviewPack || {}).reviewPairs || []).forEach((pair) => {
      const outcome = String(pair?.expectedOverall || "").trim();
      if (Object.prototype.hasOwnProperty.call(counts, outcome)) {
        counts[outcome] += 1;
      }
    });
    return counts;
  }, [benchmark]);
  const buildProcessSteps = useMemo(() => {
    const populationSummary = benchmarkPack?.populationSummary || {};
    const trialSummary = benchmarkPack?.trialSummary || {};
    const stageLabel = humanizeTargetWindow(recommendedTargetWindow || "unknown");
    const scopeCounts = recommendedTargetWindowSummary || {};
    const reviewedPairs = benchmarkReview?.overallMetrics?.reviewedPairs ?? 0;
    const agreementPct = benchmarkReview?.overallMetrics?.overallAgreementPct ?? 0;
    return [
      {
        title: "1. Curate patient posture",
        status: (populationSummary?.authoritativeBreastOncology ?? 0) > 0 ? "pass" : "unknown",
        body: scopeCounts?.authoritativePatients > 0
          ? `${scopeCounts.authoritativePatients} authoritative patients sit in ${stageLabel}, out of ${populationSummary?.authoritativeBreastOncology ?? 0} authoritative breast oncology patients.`
          : `${populationSummary?.authoritativeBreastOncology ?? 0} authoritative breast oncology patients are available for canonical matching.`,
      },
      {
        title: "2. Build trial contracts",
        status: (trialSummary?.runtimeReadyTrials ?? 0) > 0 ? "pass" : "unknown",
        body: scopeCounts?.trialCount > 0
          ? `${scopeCounts.trialCount} usable trial contracts support ${stageLabel}, with ${scopeCounts.exactTrialCount ?? scopeCounts.trialCount} in exact scope and ${scopeCounts.mixedOrManualSupportingTrialCount ?? 0} carried by mixed/manual stage coverage.`
          : `${trialSummary?.totalBreastCancerTrials ?? 0} breast cancer trials are in corpus, with ${trialSummary?.runtimeReadyTrials ?? 0} runtime-ready contracts.`,
      },
      {
        title: "3. Match in both directions",
        status: (trialSummary?.runtimeReadyDeterministicTrials ?? 0) > 0 ? "pass" : "manual",
        body: `Patient -> trials derives the lane from patient posture; trial -> patients inherits it from the selected trial contract. Mixed/manual stage trials can still support a lane, but they stay visibly marked for review. Average burden is ${trialSummary?.averageDeterministicPatientImpactingCount ?? 0} deterministic and ${trialSummary?.averageManualPatientImpactingCount ?? 0} manual patient-impacting criteria per trial.`,
      },
      {
        title: "4. Freeze proof artifacts",
        status: reviewedPairs > 0 ? "pass" : "source",
        body: `${benchmarkPack?.pairCount ?? 0} benchmark pairs are frozen for review. ${reviewedPairs} reviewed pairs currently show ${agreementPct}% agreement.`,
      },
    ];
  }, [benchmarkPack, benchmarkReview, recommendedTargetWindow, recommendedTargetWindowSummary]);

  const scorecardMetrics = useMemo(() => {
    const tc = summary?.trialCoverage ?? {};
    const pc = summary?.patientCoverage ?? {};
    return [
      { label: "Total Patients", value: pc?.totalPatients ?? 0, tone: "neutral" },
      { label: "Authoritative Cohort", value: pc?.authoritativeCohort ?? 0, tone: (pc?.authoritativeCohort ?? 0) > 0 ? "pass" : "neutral" },
      { label: "Match-Ready", value: pc?.matchReady ?? 0, tone: (pc?.matchReady ?? 0) > 0 ? "pass" : "neutral" },
      { label: "Total Trials", value: tc?.totalTrials ?? 0, tone: "neutral" },
      { label: "Runtime-Ready", value: tc?.runtimeReady ?? 0, tone: (tc?.runtimeReady ?? 0) > 0 ? "pass" : "neutral" },
      { label: "Blocked", value: tc?.blocked ?? 0, tone: (tc?.blocked ?? 0) > 0 ? "fail" : "neutral" },
    ];
  }, [summary]);

  const maxFamilyCount = useMemo(
    () => Math.max(1, ...familyCoverage.map((f) => f?.trialCount ?? f?.count ?? 0)),
    [familyCoverage],
  );

  const blockerFamilies = useMemo(
    () => [...unresolvedTop]
      .sort((a, b) => (b?.blockedTrials ?? b?.count ?? 0) - (a?.blockedTrials ?? a?.count ?? 0))
      .slice(0, 5),
    [unresolvedTop],
  );

  const matchOutcomes = useMemo(() => {
    if (!matchRuns.length) return null;
    const latest = matchRuns[0];
    const results = latest?.results ?? latest?.summary ?? {};
    return {
      counts: {
        met: results?.candidate ?? results?.met ?? 0,
        not_met: results?.excluded ?? results?.not_met ?? 0,
        unknown: results?.needs_data ?? results?.unknown ?? 0,
        manual: results?.manual_review ?? results?.manual ?? 0,
      },
      latest,
    };
  }, [matchRuns]);

  const proofRecommendations = useMemo(() => {
    const tc = summary?.trialCoverage ?? {};
    const ident = summary?.identity ?? {};
    const bl = summary?.backlog ?? {};
    const recs = [];
    if ((tc?.blocked ?? 0) > 0) {
      recs.push(`Resolve ${tc.blocked} blocked trial${tc.blocked !== 1 ? "s" : ""} to expand runtime coverage`);
    }
    if ((ident?.conflicts ?? 0) > 0) {
      recs.push(`Resolve ${ident.conflicts} linkage conflict${ident.conflicts !== 1 ? "s" : ""} for benchmark readiness`);
    }
    if (bl?.ecogGap ?? false) {
      recs.push(`Add ECOG support to cover ${bl.ecogGap} more trial${bl.ecogGap !== 1 ? "s" : ""}`);
    }
    if (unresolvedTop.length > 0) {
      const topFamily = unresolvedTop[0];
      const cnt = topFamily?.blockedTrials ?? topFamily?.count ?? 0;
      recs.push(`Resolve "${humanizeFamilyId(topFamily?.familyId ?? topFamily?.family)}" to unlock ${cnt} trial${cnt !== 1 ? "s" : ""}`);
    }
    if (!benchmarkReady) {
      recs.push("Increase authoritative coverage and resolve all blockers to reach benchmark gate");
    }
    return recs;
  }, [summary, unresolvedTop, benchmarkReady]);

  /* ---- loading / error ---- */
  if (loading) {
    return (
      <div className="pds-space">
        <SpaceHeader
          title="Governance Center"
          subtitle="Monitor coverage, blockers, benchmark readiness, and proof artifacts"
        />
        <div className="pds-spinner" />
      </div>
    );
  }

  if (error && !controlPlane) {
    return (
      <div className="pds-space">
        <SpaceHeader
          title="Governance Center"
          subtitle="Monitor coverage, blockers, benchmark readiness, and proof artifacts"
        />
        <div className="pds-error">{error}</div>
      </div>
    );
  }

  /* ---- render ---- */
  return (
    <div className="pds-space">
      {/* Header */}
      <SpaceHeader
        title="Governance Center"
        subtitle="Monitor coverage, blockers, benchmark readiness, and proof artifacts"
      />
      <div style={{ display: "flex", gap: 12, marginBottom: 24 }}>
        <button
          className="pds-btn pds-btn--primary"
          disabled={runLoading}
          onClick={handleRunEvaluation}
        >
          {runLoading ? "Running..." : "Run Evaluation"}
        </button>
      </div>
      {error && <div className="pds-error" style={{ marginBottom: 16 }}>{error}</div>}

      {/* Section 1: Corpus Proof */}
      <div className="pds-card" style={{ marginBottom: 24 }}>
        <SectionDivider label="Corpus Proof" />
        <div style={{ marginBottom: 16 }}>
          <StatusChip
            status={benchmarkReady ? "pass" : "fail"}
            label={benchmarkReady ? "Benchmark Gate: Pass" : "Benchmark Gate: Not Ready"}
          />
        </div>
        <ReadinessScorecard metrics={scorecardMetrics} />
      </div>

      {benchmark && (
        <div className="pds-card" style={{ marginBottom: 24 }}>
          <SectionDivider label="Frozen Micro Benchmark" />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
            <StatusChip
              status={benchmarkPack?.microBenchmarkGate?.ready ? "pass" : "fail"}
              label={benchmarkPack?.microBenchmarkGate?.ready ? "Micro Benchmark Ready" : "Micro Benchmark Blocked"}
            />
            <StatusChip
              status={benchmarkPack?.scaleBenchmarkGate?.ready ? "pass" : "source"}
              label={benchmarkPack?.scaleBenchmarkGate?.ready ? "Scale Benchmark Ready" : "Scale Benchmark Pending"}
            />
            <StatusChip status="manual" label="Assistant-Adjudicated Internal Gold Subset" />
          </div>

          <div className="pds-kv-grid" style={{ marginBottom: 16 }}>
            <div className="pds-kv-grid__item">
              <span className="pds-kv-grid__label">Pack Size</span>
              <span className="pds-kv-grid__value">{benchmarkPack?.pairCount ?? 0}</span>
            </div>
            <div className="pds-kv-grid__item">
              <span className="pds-kv-grid__label">Reviewed Pairs</span>
              <span className="pds-kv-grid__value">{benchmarkReview?.overallMetrics?.reviewedPairs ?? 0}</span>
            </div>
            <div className="pds-kv-grid__item">
              <span className="pds-kv-grid__label">Agreement</span>
              <span className="pds-kv-grid__value">{benchmarkReview?.overallMetrics?.overallAgreementPct ?? 0}%</span>
            </div>
            <div className="pds-kv-grid__item">
              <span className="pds-kv-grid__label">Outcome Mix</span>
              <span className="pds-kv-grid__value">
                {[
                  `C ${reviewedOutcomeMix.candidate}`,
                  `E ${reviewedOutcomeMix.excluded}`,
                  `M ${reviewedOutcomeMix.manual_review_required}`,
                ].join(" · ")}
              </span>
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <p className="pds-muted" style={{ margin: 0 }}>
                {benchmark?.currentCaveat || "Benchmark artifacts are frozen for reproducible review and pending SME adjudication."}
              </p>
              <p className="pds-muted" style={{ margin: 0, fontSize: 12 }}>
                Artifact commit: {benchmarkPack?.workspaceVersion?.gitCommit || benchmarkPack?.gitCommit || benchmark?.pack?.gitCommit || "unknown"}
              </p>
            </div>
            <a className="pds-btn pds-btn--secondary" href={reviewSheetUrl} target="_blank" rel="noreferrer">
              Open Review Sheet
            </a>
          </div>

          <SectionDivider label="Demonstrator Scope" />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginBottom: 16 }}>
            <div className="pds-card" style={{ margin: 0 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                  <strong>What This Pack Supports</strong>
                  <StatusChip
                    status={targetWindowStatus(recommendedTargetWindow)}
                    label={recommendedTargetWindow ? `Best Supported: ${humanizeTargetWindow(recommendedTargetWindow)}` : "Best Supported: Pending"}
                    size="sm"
                  />
                </div>
                <div className="pds-muted" style={{ fontSize: 13 }}>
                  The same UI supports both matching directions. Patient {"->"} trials derives the lane from patient posture, while trial {"->"} patients inherits it from the selected trial contract.
                </div>
                {benchmarkQueryProfile?.label ? (
                  <div className="pds-muted" style={{ fontSize: 13 }}>
                    Planned corpus slice: {benchmarkQueryProfile.label}. {benchmarkQueryProfile?.description || ""}
                  </div>
                ) : null}
                {queryTargetWindowGoal ? (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    <StatusChip
                      status={targetWindowStatus(queryTargetWindowGoal)}
                      label={`Planned Lane: ${humanizeTargetWindow(queryTargetWindowGoal)}`}
                      size="sm"
                    />
                    {queryTargetWindowGoalSummary ? (
                      <StatusChip
                        status="neutral"
                        label={`Usable Trials in Planned Lane ${queryTargetWindowGoalSummary?.trialCount ?? 0}`}
                        size="sm"
                      />
                    ) : null}
                  </div>
                ) : null}
                <div className="pds-muted" style={{ fontSize: 13 }}>
                  {stageScopeNarrative}
                </div>
                {recommendedTargetWindowSummary ? (
                  <>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <StatusChip status="pass" label={`Authoritative ${recommendedTargetWindowSummary?.authoritativePatients ?? 0}`} size="sm" />
                      <StatusChip status="canonical" label={`Match-ready ${recommendedTargetWindowSummary?.matchReadyPatients ?? 0}`} size="sm" />
                      <StatusChip status="source" label={`Usable Trials ${recommendedTargetWindowSummary?.trialCount ?? 0}`} size="sm" />
                      <StatusChip status="pass" label={`Runtime-ready ${recommendedTargetWindowSummary?.runtimeReadyTrials ?? 0}`} size="sm" />
                      <StatusChip status="canonical" label={`Deterministic ${recommendedTargetWindowSummary?.runtimeReadyDeterministicTrials ?? 0}`} size="sm" />
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <StatusChip status="neutral" label={`Exact Scope ${recommendedTargetWindowSummary?.exactTrialCount ?? 0}`} size="sm" />
                      <StatusChip status="manual" label={`Mixed / Manual Support ${recommendedTargetWindowSummary?.mixedOrManualSupportingTrialCount ?? 0}`} size="sm" />
                    </div>
                    {(recommendedTargetWindowSummary?.mixedOrManualSupportingTrialCount ?? 0) > 0 && (
                      <div className="pds-muted" style={{ fontSize: 13 }}>
                        Some usable trials are carried by mixed/manual stage scope. They remain visible for review, but the runtime can still use them inside this lane.
                      </div>
                    )}
                    {recommendedExactWindowSummary && (recommendedExactWindowSummary?.runtimeReadyTrials ?? 0) === 0 && (recommendedTargetWindowSummary?.runtimeReadyTrials ?? 0) > 0 && (
                      <div className="pds-muted" style={{ fontSize: 13 }}>
                        Exact stage overlap is still thin here; the current demonstrator relies on mixed/manual trial scope support more than strict stage-labeled trial contracts.
                      </div>
                    )}
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <StatusChip
                        status={scaleBenchmarkGate?.ready ? "pass" : "manual"}
                        label={scaleBenchmarkGate?.ready ? "Scale Benchmark Ready" : "Scale Benchmark Not Ready"}
                        size="sm"
                      />
                      <StatusChip status="neutral" label={scaleGateSummary.patientText} size="sm" />
                      <StatusChip status="neutral" label={scaleGateSummary.trialText} size="sm" />
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <StatusChip
                        status={scaleBenchmarkGate?.ready ? "pass" : "manual"}
                        label={scaleBenchmarkGate?.ready ? "Scale Benchmark Ready" : "Coverage Expansion Needed"}
                        size="sm"
                      />
                      <StatusChip status="neutral" label={scaleGateSummary.patientText} size="sm" />
                      <StatusChip status="neutral" label={scaleGateSummary.trialText} size="sm" />
                    </div>
                    <div className="pds-muted" style={{ fontSize: 13 }}>
                      The safest next improvement is data coverage, not new architecture.
                    </div>
                  </>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  <button
                    type="button"
                    className="pds-btn pds-btn--secondary"
                    onClick={() => navigateSpace("matching", {
                      direction: "trial_to_patients",
                      stageTargetWindow: recommendedTargetWindow || "stage_ii_iii_or_locally_advanced",
                      launchSource: "control",
                    })}
                  >
                    {recommendedTargetWindow ? "Open Best Supported Lane" : "Inspect Stage II/III Scope"}
                  </button>
                  {queryTargetWindowGoal && queryTargetWindowGoal !== recommendedTargetWindow && (
                    <button
                      type="button"
                      className="pds-btn pds-btn--ghost"
                      onClick={() => navigateSpace("matching", {
                        direction: "trial_to_patients",
                        stageTargetWindow: queryTargetWindowGoal,
                        launchSource: "control",
                      })}
                    >
                      Open Planned Lane
                    </button>
                  )}
                  <button
                    type="button"
                    className="pds-btn pds-btn--ghost"
                    onClick={() => navigateSpace("matching", {
                      direction: "patient_to_trials",
                      launchSource: "control",
                    })}
                  >
                    Open Patient Matching
                  </button>
                </div>
              </div>
            </div>

            <div className="pds-card" style={{ margin: 0 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <strong>Patients by Stage</strong>
                {patientStageWindows.length > 0 ? patientStageWindows.map((row) => (
                  <div key={row?.targetWindow} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                      <StatusChip
                        status={targetWindowStatus(row?.targetWindow)}
                        label={humanizeTargetWindow(row?.targetWindow)}
                        size="sm"
                      />
                      <span className="pds-muted" style={{ fontSize: 12 }}>{row?.count ?? 0} patients</span>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <StatusChip status="pass" label={`Authoritative ${row?.authoritativeCount ?? 0}`} size="sm" />
                      <StatusChip status="canonical" label={`Benchmark-ready ${row?.matchReadyCount ?? 0}`} size="sm" />
                      <StatusChip status="source" label={`Outreach-ready ${row?.retailOutreachReadyCount ?? 0}`} size="sm" />
                    </div>
                  </div>
                )) : (
                  <p className="pds-muted" style={{ margin: 0 }}>No stage-aware patient posture has been frozen into this pack yet.</p>
                )}
              </div>
            </div>

            <div className="pds-card" style={{ margin: 0 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <strong>Trials by Exact Scope</strong>
                {trialStageWindows.length > 0 ? trialStageWindows.map((row) => (
                  <div key={row?.targetWindow} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                      <StatusChip
                        status={targetWindowStatus(row?.targetWindow)}
                        label={humanizeTargetWindow(row?.targetWindow)}
                        size="sm"
                      />
                      <span className="pds-muted" style={{ fontSize: 12 }}>{row?.count ?? 0} trials</span>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <StatusChip status="pass" label={`Runtime-ready ${row?.runtimeReadyCount ?? 0}`} size="sm" />
                      <StatusChip status="canonical" label={`Deterministic ${row?.runtimeReadyDeterministicCount ?? 0}`} size="sm" />
                      <StatusChip status="manual" label={`Manual ${row?.runtimeReadyWithManualCount ?? 0}`} size="sm" />
                    </div>
                  </div>
                )) : (
                  <p className="pds-muted" style={{ margin: 0 }}>No stage-aware trial scope has been frozen into this pack yet.</p>
                )}
              </div>
            </div>

            <div className="pds-card" style={{ margin: 0 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <strong>Usable Demo Lanes</strong>
                {demoLaneWindows.length > 0 ? demoLaneWindows.map((row) => (
                  <div key={row?.targetWindow} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                      <StatusChip
                        status={targetWindowStatus(row?.targetWindow)}
                        label={humanizeTargetWindow(row?.targetWindow)}
                        size="sm"
                      />
                      <span className="pds-muted" style={{ fontSize: 12 }}>{row?.patientCount ?? 0} patients in lane</span>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      <StatusChip status="source" label={`Usable Trials ${row?.trialCount ?? 0}`} size="sm" />
                      <StatusChip status="neutral" label={`Exact Scope ${row?.exactTrialCount ?? 0}`} size="sm" />
                      <StatusChip status="manual" label={`Mixed / Manual ${row?.mixedOrManualSupportingTrialCount ?? 0}`} size="sm" />
                    </div>
                    <div className="pds-muted" style={{ fontSize: 13 }}>
                      {row?.matchReadyPatients ?? 0} match-ready patients and {row?.runtimeReadyTrials ?? 0} runtime-ready trials can currently be demonstrated in this lane.
                    </div>
                  </div>
                )) : (
                  <p className="pds-muted" style={{ margin: 0 }}>No usable demo-lane summary has been frozen into this pack yet.</p>
                )}
              </div>
            </div>
          </div>

          <SectionDivider label="Build Process" />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginBottom: 16 }}>
            {buildProcessSteps.map((step) => (
              <div key={step.title} className="pds-card" style={{ margin: 0 }}>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <strong>{step.title}</strong>
                    <StatusChip status={step.status} size="sm" />
                  </div>
                  <div className="pds-muted" style={{ fontSize: 13 }}>
                    {step.body}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <SectionDivider label="Validation Status" />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
            <div className="pds-card" style={{ margin: 0 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <strong>Internal Benchmark</strong>
                  <StatusChip status="manual" label="Assistant-Adjudicated" size="sm" />
                </div>
                <div className="pds-muted" style={{ fontSize: 13 }}>
                  Agreement {benchmarkReview?.overallMetrics?.overallAgreementPct ?? 0}% across {benchmarkReview?.overallMetrics?.reviewedPairs ?? 0} reviewed pairs.
                </div>
              </div>
            </div>

            <div className="pds-card" style={{ margin: 0 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <strong>SME Validation</strong>
                  <StatusChip
                    status={benchmarkSmeReview?.summary?.completedReviews ? "pass" : "unknown"}
                    label={benchmarkSmeReview?.summary?.completedReviews ? "SME Review Loaded" : "Pending SME Review"}
                    size="sm"
                  />
                </div>
                <div className="pds-muted" style={{ fontSize: 13 }}>
                  {benchmarkSmeReview
                    ? `Completed ${benchmarkSmeReview?.summary?.completedReviews ?? 0}/${benchmarkSmeReview?.summary?.totalReviewablePairs ?? 0} pairs${benchmarkSmeReview?.summary?.completedReviews ? ` · Agreement ${benchmarkSmeReview?.summary?.overallAgreementPct ?? 0}%` : ""}.`
                    : "No SME-reviewed benchmark artifact has been imported yet."}
                </div>
                {benchmarkSmeReview?.disagreementTaxonomy?.length > 0 && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {benchmarkSmeReview.disagreementTaxonomy.slice(0, 3).map((row) => (
                      <StatusChip
                        key={row?.taxonomy}
                        status="unknown"
                        label={`${row?.taxonomy}: ${row?.count}`}
                        size="sm"
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Section 2: Trial Coverage */}
      <div className="pds-card" style={{ marginBottom: 24 }}>
        <SectionDivider label="Trial Coverage" />
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {familyCoverage.map((family, idx) => {
            const familyId = family?.familyId ?? family?.family ?? `family-${idx}`;
            const name = humanizeFamilyId(familyId);
            const status = family?.computability ?? family?.status ?? "unresolved";
            const count = family?.trialCount ?? family?.count ?? 0;
            const barWidth = (count / maxFamilyCount) * 100;
            return (
              <div
                key={familyId}
                style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 32 }}
              >
                <span style={{ flex: 1, fontWeight: 600, fontSize: 14 }}>{name}</span>
                <StatusChip status={status} size="sm" />
                <span className="pds-chip pds-chip--neutral pds-chip--sm"
                  style={{ minWidth: 40, textAlign: "center" }}
                >
                  {count}
                </span>
                <div style={{ width: 120, height: 8, background: "var(--pharma-surface, #e8edf2)", borderRadius: 4, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${barWidth}%`,
                      height: "100%",
                      background: status === "deterministic" ? "var(--pharma-runtime, #2b7a5f)"
                        : status === "manual" ? "var(--pharma-manual, #d4a843)"
                        : "var(--pharma-unknown, #94a3b8)",
                      borderRadius: 4,
                      transition: "width 0.3s ease",
                    }}
                  />
                </div>
              </div>
            );
          })}
          {familyCoverage.length === 0 && (
            <p className="pds-muted">No family coverage data available yet.</p>
          )}
        </div>

        <SectionDivider label="Top Blockers" />
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {blockerFamilies.map((family, idx) => {
            const familyId = family?.familyId ?? family?.family ?? `blocker-${idx}`;
            const count = family?.blockedTrials ?? family?.count ?? 0;
            return (
              <div key={familyId} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ flex: 1, fontWeight: 500, fontSize: 14 }}>
                  {humanizeFamilyId(familyId)}
                </span>
                <span style={{ fontWeight: 700, color: "var(--pharma-fail, #b91c1c)" }}>{count}</span>
                <span className="pds-muted" style={{ fontSize: 13 }}>trials blocked</span>
              </div>
            );
          })}
          {blockerFamilies.length === 0 && (
            <p className="pds-muted">No unresolved blockers.</p>
          )}
        </div>
      </div>

      {/* Section 3: Patient Evidence Coverage */}
      <div className="pds-card" style={{ marginBottom: 24 }}>
        <SectionDivider label="Patient Evidence Coverage" />
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {EVIDENCE_CATEGORIES.map((cat) => {
            const pct = Number(ev?.[cat.key] ?? ev?.[cat.key + "Pct"] ?? 0);
            return (
              <div key={cat.key} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ width: 160, fontWeight: 500, fontSize: 14 }}>{cat.label}</span>
                <div style={{ flex: 1, height: 10, background: "var(--pharma-surface, #e8edf2)", borderRadius: 5, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${Math.min(100, pct)}%`,
                      height: "100%",
                      background: "var(--pharma-runtime, #2b7a5f)",
                      borderRadius: 5,
                      transition: "width 0.3s ease",
                    }}
                  />
                </div>
                <span style={{ width: 50, textAlign: "right", fontWeight: 600, fontSize: 13 }}>
                  {pct.toFixed(0)}%
                </span>
              </div>
            );
          })}
        </div>

        <div style={{ marginTop: 20 }}>
          <div className="pds-kv-grid">
            <div className="pds-kv-grid__item">
              <span className="pds-kv-grid__label">Linked</span>
              <span className="pds-kv-grid__value">{identity?.linked ?? 0}</span>
            </div>
            <div className="pds-kv-grid__item">
              <span className="pds-kv-grid__label">Unlinked</span>
              <span className="pds-kv-grid__value">{identity?.unlinked ?? 0}</span>
            </div>
            <div className="pds-kv-grid__item">
              <span className="pds-kv-grid__label">Conflicts</span>
              <span className="pds-kv-grid__value">{identity?.conflicts ?? 0}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Section 4: Match Outcomes */}
      <div className="pds-card" style={{ marginBottom: 24 }}>
        <SectionDivider label="Match Outcomes" />
        {matchOutcomes ? (
          <>
            <CriterionStatusBar counts={matchOutcomes.counts} />
            <div className="pds-kv-grid" style={{ marginTop: 16 }}>
              <div className="pds-kv-grid__item">
                <span className="pds-kv-grid__label">Candidate</span>
                <span className="pds-kv-grid__value">{matchOutcomes.counts.met}</span>
              </div>
              <div className="pds-kv-grid__item">
                <span className="pds-kv-grid__label">Excluded</span>
                <span className="pds-kv-grid__value">{matchOutcomes.counts.not_met}</span>
              </div>
              <div className="pds-kv-grid__item">
                <span className="pds-kv-grid__label">Needs Data</span>
                <span className="pds-kv-grid__value">{matchOutcomes.counts.unknown}</span>
              </div>
              <div className="pds-kv-grid__item">
                <span className="pds-kv-grid__label">Manual Review</span>
                <span className="pds-kv-grid__value">{matchOutcomes.counts.manual}</span>
              </div>
            </div>

            <SectionDivider label="Recent Runs" />
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {matchRuns.slice(0, 5).map((run, idx) => {
                const date = run?.createdAt ?? run?.timestamp ?? run?.date;
                const direction = run?.direction ?? run?.mode ?? "patient_to_trials";
                const dirLabel = direction === "trial_to_patients" ? "Trial \u2192 Patients" : "Patient \u2192 Trials";
                const runStatus = run?.status ?? "completed";
                const resultCount = run?.resultCount ?? run?.results?.length ?? 0;
                return (
                  <div
                    key={run?.id ?? idx}
                    style={{
                      display: "flex", alignItems: "center", gap: 12,
                      padding: "8px 12px", borderRadius: 8,
                      background: "var(--pharma-surface, #f4f6f8)",
                    }}
                  >
                    <span style={{ fontSize: 13, color: "var(--pharma-muted, #64748b)", minWidth: 140 }}>
                      {date ? new Date(date).toLocaleString() : "N/A"}
                    </span>
                    <StatusChip status="source" label={dirLabel} size="sm" />
                    <span style={{ flex: 1, fontSize: 13 }}>
                      {resultCount} result{resultCount !== 1 ? "s" : ""}
                    </span>
                    <StatusChip status={runStatus === "completed" ? "pass" : runStatus} size="sm" />
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <p className="pds-muted">No match runs recorded yet. Run a match from the Matching Studio to populate outcomes.</p>
        )}
      </div>

      {/* Section 5: Semantic Backlog */}
      <div className="pds-card" style={{ marginBottom: 24 }}>
        <SectionDivider label="Semantic Backlog" />

        {/* Top Unresolved Families */}
        <h4 className="pds-label" style={{ marginBottom: 8 }}>Top Unresolved Families</h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 20 }}>
          {unresolvedTop.length > 0 ? unresolvedTop.map((family, idx) => {
            const familyId = family?.familyId ?? family?.family ?? `unresolved-${idx}`;
            const count = family?.blockedTrials ?? family?.count ?? 0;
            return (
              <div key={familyId} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ flex: 1, fontSize: 14 }}>{humanizeFamilyId(familyId)}</span>
                <span className="pds-chip pds-chip--not-met pds-chip--sm">{count} trial{count !== 1 ? "s" : ""}</span>
              </div>
            );
          }) : (
            <p className="pds-muted">No unresolved families.</p>
          )}
        </div>

        {/* Top Patient Evidence Gaps */}
        <h4 className="pds-label" style={{ marginBottom: 8 }}>Top Patient Evidence Gaps</h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 20 }}>
          {(backlog?.evidenceGaps ?? []).length > 0 ? backlog.evidenceGaps.map((gap, idx) => (
            <div key={gap?.feature ?? idx} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ flex: 1, fontSize: 14 }}>{gap?.feature ?? gap?.label ?? "Unknown"}</span>
              <span className="pds-chip pds-chip--unknown pds-chip--sm">
                {gap?.patientCount ?? gap?.count ?? 0} patient{(gap?.patientCount ?? gap?.count ?? 0) !== 1 ? "s" : ""}
              </span>
            </div>
          )) : (
            <p className="pds-muted">No evidence gaps recorded.</p>
          )}
        </div>

        {/* Benchmark Blockers */}
        <h4 className="pds-label" style={{ marginBottom: 8 }}>Benchmark Blockers</h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 20 }}>
          {(backlog?.benchmarkBlockers ?? []).length > 0 ? backlog.benchmarkBlockers.map((blocker, idx) => (
            <div key={idx} style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <StatusChip status="fail" size="sm" />
              <span style={{ fontSize: 14 }}>{blocker?.description ?? blocker}</span>
            </div>
          )) : (
            benchmarkReady ? (
              <p className="pds-muted">All benchmark gates passed.</p>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <StatusChip status="fail" size="sm" />
                <span style={{ fontSize: 14 }}>Benchmark gate not yet satisfied</span>
              </div>
            )
          )}
        </div>

        {/* Proof Recommendations */}
        <h4 className="pds-label" style={{ marginBottom: 8 }}>Proof Recommendations</h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {proofRecommendations.length > 0 ? proofRecommendations.map((rec, idx) => (
            <div key={idx} style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="var(--pharma-runtime, #2b7a5f)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, marginTop: 2 }}>
                <path d="M10 2a5 5 0 013 9v2a1 1 0 01-1 1H8a1 1 0 01-1-1v-2a5 5 0 013-9z" />
                <line x1="8" y1="16" x2="12" y2="16" />
                <line x1="9" y1="18" x2="11" y2="18" />
              </svg>
              <span style={{ fontSize: 14 }}>{rec}</span>
            </div>
          )) : (
            <p className="pds-muted">No recommendations at this time.</p>
          )}
        </div>
      </div>
    </div>
  );
}
