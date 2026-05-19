import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  APIGetClinicalTrialsHealth,
  APIApplyClinicalTrialReview,
  APIBulkIngestClinicalTrials,
  APIGetClinicalTrial,
  APIGetClinicalTrialCriteria,
  APIGetClinicalTrialFamilyCoverage,
  APIGetClinicalTrialReviewQueue,
  APIGetClinicalTrialRun,
  APIGetClinicalTrialUnresolvedTop,
  APIIngestClinicalTrial,
  APIListClinicalTrialCriteria,
  APIListClinicalTrialRuleFamilies,
  APIListClinicalTrialRuns,
  APIListClinicalTrials,
  APIRecompileClinicalTrial,
  APIRecompileClinicalTrialFamily,
  APISearchClinicalTrials,
} from "../../services/APIService";

const TERMINAL_RUN_STATUSES = new Set(["completed", "completed_with_review", "failed", "skipped_unchanged"]);
const DEFAULT_SEARCH_FORM = {
  condition: "breast cancer",
  phase: "PHASE3",
  sponsor: "",
  nctId: "",
};
const WORKBENCH_WIZARD_STEPS = [
  {
    id: "ingest",
    stepLabel: "Step 1",
    title: "Find And Queue",
    description: "Search ClinicalTrials.gov and start a single-trial or bulk ingestion run.",
  },
  {
    id: "run",
    stepLabel: "Step 2",
    title: "Watch The Pipeline",
    description: "See what the ingestor is doing in plain language, stage by stage.",
  },
  {
    id: "contract",
    stepLabel: "Step 3",
    title: "Review Contract",
    description: "Inspect the stored trial document, the generated contract, and the next matching actions.",
  },
  {
    id: "review",
    stepLabel: "Step 4",
    title: "Safe Review",
    description: "Understand the small set of criteria the compiler intentionally refused to guess on.",
  },
  {
    id: "inspect",
    stepLabel: "Step 5",
    title: "Advanced Inspector",
    description: "Deep dive into individual criteria and reusable rule families.",
  },
];
const STAGE_LABELS = {
  fetch_source: "Fetch Source",
  search_remote: "Search Remote Query",
  ingest_trials: "Ingest Query Trials",
  summarize_corpus: "Summarize Corpus",
  load_version: "Load Stored Version",
  parse_structure: "Parse Structure",
  compile_criteria: "Compile Criteria",
  materialize_match_contract: "Materialize Match Contract",
  select_trials: "Select Family Trials",
  recompile_trials: "Recompile Trials",
  summarize_coverage: "Summarize Coverage",
  apply_review: "Apply Review",
  trigger_recompile: "Trigger Recompile",
};
const STAGE_EXPLANATIONS = {
  fetch_source: "Copy the study from ClinicalTrials.gov and freeze the exact source version we will compile.",
  search_remote: "Search ClinicalTrials.gov for a corpus slice that matches the current query.",
  ingest_trials: "Queue the selected trials for ingestion and immutable versioning.",
  summarize_corpus: "Refresh corpus-level coverage after the ingest finishes.",
  load_version: "Load the stored source version already frozen in the local runtime.",
  parse_structure: "Turn raw eligibility text into structured source nodes we can inspect and compile.",
  compile_criteria: "Assign candidate rule families, validate them, and flag only the criteria that truly need review.",
  materialize_match_contract: "Write the canonical contract used later by patient-to-trial and trial-to-patient matching.",
  select_trials: "Resolve which trials belong to the chosen corpus or family refresh scope.",
  recompile_trials: "Re-run compilation for the chosen trial set with the latest registry and reviewer decisions.",
  summarize_coverage: "Update family coverage and backlog analytics after recompilation.",
  apply_review: "Persist a reviewer decision so the compiler uses it on the next pass.",
  trigger_recompile: "Launch the next compile pass so the new decision becomes part of the contract.",
};
const DEFAULT_REVIEW_FORM = {
  decision: "confirm_family",
  finalFamilyId: "",
  reviewer: "analyst",
  notes: "",
  finalCompiledRuleText: "",
  recompileTarget: "trial",
};
const LOCAL_CLINICAL_TRIALS_START_COMMAND = "npm --prefix app run start:clinical-trials-service";

const formatDateTime = (value) => {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
};

const formatCount = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "0";
  return numeric.toLocaleString();
};

const formatJson = (value) => JSON.stringify(value || {}, null, 2);
const formatJsonBlock = (value) => JSON.stringify(value || {}, null, 2);
const truncateText = (value, maxLength = 220) => {
  const text = String(value || "").trim();
  if (!text) return "-";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength).trim()}...`;
};

const titleCase = (value) =>
  String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());

const buildStatusTone = (status) => {
  const normalized = String(status || "").toLowerCase();
  if (["completed", "ready_auto"].includes(normalized)) return "pass";
  if (["completed_with_review", "ready_with_review", "running", "queued"].includes(normalized)) return "review";
  if (["failed"].includes(normalized)) return "blocked";
  if (["skipped_unchanged"].includes(normalized)) return "neutral";
  return "neutral";
};

const buildStagePercent = (stage) => {
  const status = String(stage?.status || "").toLowerCase();
  if (["completed", "skipped"].includes(status)) return 100;
  if (status === "running") return 58;
  if (status === "failed") return 100;
  return 0;
};

const buildOverallPercent = (run) => {
  const stages = Array.isArray(run?.stages) ? run.stages : [];
  if (stages.length === 0) return 0;
  const totalUnits = stages.length * 100;
  const completedUnits = stages.reduce((sum, stage) => sum + buildStagePercent(stage), 0);
  return Math.max(0, Math.min(100, Math.round((completedUnits / totalUnits) * 100)));
};

const normalizeCriteriaRows = (criteria) =>
  Array.isArray(criteria)
    ? [...criteria].sort((left, right) => {
        const leftSection = String(left?.section || "");
        const rightSection = String(right?.section || "");
        if (leftSection !== rightSection) return leftSection.localeCompare(rightSection);
        return Number(left?.ordinal || 0) - Number(right?.ordinal || 0);
      })
    : [];

const StageMetricList = ({ metrics }) => {
  const entries = Object.entries(metrics || {}).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (entries.length === 0) {
    return <span className="ct-workbench-empty-inline">No metrics yet</span>;
  }
  return (
    <div className="ct-stage-metrics">
      {entries.map(([key, value]) => (
        <span key={key} className="ct-stage-metric-pill">
          <strong>{titleCase(key)}</strong>
          <span>{typeof value === "number" ? formatCount(value) : String(value)}</span>
        </span>
      ))}
    </div>
  );
};

const FamilyCoverageBars = ({ rows, onSelectFamily }) => {
  const topRows = Array.isArray(rows) ? rows.slice(0, 8) : [];
  if (topRows.length === 0) {
    return <div className="ct-workbench-empty">Coverage metrics will appear after ingestion.</div>;
  }
  const maxTotal = Math.max(...topRows.map((row) => Number(row?.total || 0)), 1);
  return (
    <div className="ct-coverage-bars">
      {topRows.map((row) => {
        const autoCompiled = Number(row?.statuses?.auto_compiled || 0);
        const needsReview = Number(row?.statuses?.needs_review || 0);
        const contextOnly = Number(row?.statuses?.context_only || 0);
        const total = Number(row?.total || 0);
        const widthPct = Math.max(8, Math.round((total / maxTotal) * 100));
        return (
          <button
            type="button"
            key={row.family_id}
            className="ct-coverage-row"
            onClick={() => onSelectFamily?.(row.family_id)}
          >
            <div className="ct-coverage-head">
              <span>{row.family_id}</span>
              <strong>{formatCount(total)}</strong>
            </div>
            <div className="ct-coverage-track">
              <div className="ct-coverage-fill auto" style={{ width: `${Math.max(0, (autoCompiled / Math.max(total, 1)) * widthPct)}%` }} />
              <div className="ct-coverage-fill review" style={{ width: `${Math.max(0, (needsReview / Math.max(total, 1)) * widthPct)}%` }} />
              <div className="ct-coverage-fill neutral" style={{ width: `${Math.max(0, (contextOnly / Math.max(total, 1)) * widthPct)}%` }} />
            </div>
            <div className="ct-coverage-meta">
              <span>Auto {formatCount(autoCompiled)}</span>
              <span>Review {formatCount(needsReview)}</span>
              <span>Context {formatCount(contextOnly)}</span>
            </div>
          </button>
        );
      })}
    </div>
  );
};

const normalizeReviewFormFromCriterion = (criterion) => {
  const compiler = criterion?.compiler || {};
  const review = criterion?.review || {};
  return {
    decision: review?.decision || "confirm_family",
    finalFamilyId:
      review?.final_family_id
      || compiler?.selected_family_id
      || compiler?.candidate_families?.[0]?.family_id
      || "",
    reviewer: review?.reviewer || "analyst",
    notes: review?.notes || "",
    finalCompiledRuleText:
      review?.final_compiled_rule
      ? formatJsonBlock(review.final_compiled_rule)
      : compiler?.compiled_rule
        ? formatJsonBlock(compiler.compiled_rule)
        : "",
    recompileTarget: "trial",
  };
};

const normalizeClinicalTrialsError = (error, fallback) => {
  const status = Number(error?.response?.status || 0);
  const detail = error?.response?.data?.detail || error?.response?.data?.error || error?.message || "";
  if ([404, 502, 503, 504].includes(status) || !error?.response) {
    return `${fallback} The clinical trials service is not responding. Start it with "${LOCAL_CLINICAL_TRIALS_START_COMMAND}" and refresh this workspace.`;
  }
  return detail || fallback;
};

const summarizeReviewReason = (row) => {
  const reviewStatus = String(row?.review?.status || "").toLowerCase();
  const parseStatus = String(row?.compiler?.parse_status || "").toLowerCase();
  if (reviewStatus === "pending") {
    return row?.review?.notes || "Held for reviewer confirmation because this criterion contains more than one plausible clinical interpretation.";
  }
  if (parseStatus === "needs_review") {
    return "The compiler found a plausible family but kept this criterion for review rather than guessing.";
  }
  if (parseStatus === "manual_classified") {
    return "This criterion currently stays in a manual family because a reviewer should decide the safest interpretation.";
  }
  return "Queued for human adjudication to keep the demonstrator conservative and credible.";
};

export default function ClinicalTrialsWorkbench({
  onContinueToTrialScreening,
  onContinueToPatientMatching,
}) {
  const [wizardStep, setWizardStep] = useState("ingest");
  const [searchForm, setSearchForm] = useState(DEFAULT_SEARCH_FORM);
  const [searchLoading, setSearchLoading] = useState(false);
  const [bulkIngestLoading, setBulkIngestLoading] = useState(false);
  const [searchResults, setSearchResults] = useState([]);
  const [ingestError, setIngestError] = useState("");
  const [ingestingTrialId, setIngestingTrialId] = useState("");

  const [recentRuns, setRecentRuns] = useState([]);
  const [recentTrials, setRecentTrials] = useState([]);
  const [activeRunId, setActiveRunId] = useState("");
  const [activeRun, setActiveRun] = useState(null);
  const [runLoading, setRunLoading] = useState(false);

  const [selectedTrialId, setSelectedTrialId] = useState("");
  const [selectedTrial, setSelectedTrial] = useState(null);
  const [criteriaRows, setCriteriaRows] = useState([]);
  const [selectedCriterionId, setSelectedCriterionId] = useState("");
  const [criteriaLoading, setCriteriaLoading] = useState(false);

  const [ruleFamilies, setRuleFamilies] = useState([]);
  const [selectedFamilyId, setSelectedFamilyId] = useState("");
  const [unresolvedRows, setUnresolvedRows] = useState([]);
  const [familyCoverageRows, setFamilyCoverageRows] = useState([]);
  const [clusterRows, setClusterRows] = useState([]);
  const [selectedFingerprint, setSelectedFingerprint] = useState("");
  const [reviewQueueRows, setReviewQueueRows] = useState([]);
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewForm, setReviewForm] = useState(DEFAULT_REVIEW_FORM);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [objectExplorerTab, setObjectExplorerTab] = useState("trial");
  const [serviceHealth, setServiceHealth] = useState({
    checking: true,
    ok: false,
    message: "Checking clinical trials service...",
  });

  const sortedCriteriaRows = useMemo(() => normalizeCriteriaRows(criteriaRows), [criteriaRows]);

  const selectedCriterion = useMemo(
    () => sortedCriteriaRows.find((row) => row?._id === selectedCriterionId) || sortedCriteriaRows[0] || null,
    [selectedCriterionId, sortedCriteriaRows]
  );

  const selectedFamily = useMemo(() => {
    const preferredFamilyId =
      selectedFamilyId
      || selectedCriterion?.compiler?.selected_family_id
      || selectedCriterion?.compiler?.candidate_families?.[0]?.family_id
      || "";
    return ruleFamilies.find((row) => row?.family_id === preferredFamilyId) || ruleFamilies[0] || null;
  }, [ruleFamilies, selectedFamilyId, selectedCriterion]);

  const overallRunPercent = useMemo(() => buildOverallPercent(activeRun), [activeRun]);
  const selectedTrialIdValue = selectedTrial?._id || selectedTrial?.trialId || "";
  const selectedTrialRunnable = Boolean(selectedTrial?.runtimeReadiness?.runnable);
  const pipelineStageGuide = useMemo(() => {
    const runStageMap = new Map((activeRun?.stages || []).map((stage) => [stage.name, stage]));
    return ["fetch_source", "parse_structure", "compile_criteria", "materialize_match_contract"].map((stageName) => ({
      stageName,
      label: STAGE_LABELS[stageName] || titleCase(stageName),
      description: STAGE_EXPLANATIONS[stageName] || "This pipeline stage prepares the final canonical contract.",
      stage: runStageMap.get(stageName) || null,
    }));
  }, [activeRun]);
  const availableObjectTabs = useMemo(
    () => [
      {
        id: "trial",
        label: "Full Trial Document",
        description: "The exact ingested trial document currently stored in the runtime.",
        payload: selectedTrial,
      },
      {
        id: "run",
        label: "Run Object",
        description: "The operational run record that explains how this ingest executed.",
        payload: activeRun,
      },
    ],
    [selectedTrial, activeRun]
  );
  const activeObjectTab =
    availableObjectTabs.find((tab) => tab.id === objectExplorerTab && tab.payload)
    || availableObjectTabs.find((tab) => tab.payload)
    || availableObjectTabs[0];
  const selectedTrialReviewPendingCount = Number(selectedTrial?.compile?.review_pending_count || 0);
  const selectedTrialDeterministicCount = Number(selectedTrial?.criteria_summary?.deterministic_rules || 0);

  const refreshServiceHealth = useCallback(async () => {
    setServiceHealth((previous) => ({
      ...previous,
      checking: true,
    }));
    try {
      const response = await APIGetClinicalTrialsHealth();
      const ok = Boolean(response?.ok);
      setServiceHealth({
        checking: false,
        ok,
        message: ok
          ? "Clinical trials ingestion sidecar is available on the canonical runtime path."
          : "Clinical trials service did not return a healthy response.",
      });
      return ok;
    } catch (error) {
      setServiceHealth({
        checking: false,
        ok: false,
        message: normalizeClinicalTrialsError(error, "Clinical trials service unavailable."),
      });
      return false;
    }
  }, []);

  const refreshWorkbenchSummary = useCallback(async () => {
    const [runsResponse, trialsResponse, ruleFamiliesResponse, unresolvedResponse, coverageResponse, reviewQueueResponse] = await Promise.allSettled([
      APIListClinicalTrialRuns({ limit: 8 }),
      APIListClinicalTrials({ limit: 8 }),
      APIListClinicalTrialRuleFamilies(),
      APIGetClinicalTrialUnresolvedTop({ limit: 12 }),
      APIGetClinicalTrialFamilyCoverage(),
      APIGetClinicalTrialReviewQueue({ limit: 16 }),
    ]);

    if (runsResponse.status === "fulfilled") {
      setRecentRuns(Array.isArray(runsResponse.value?.runs) ? runsResponse.value.runs : []);
    }
    if (trialsResponse.status === "fulfilled") {
      setRecentTrials(Array.isArray(trialsResponse.value?.trials) ? trialsResponse.value.trials : []);
    }
    if (ruleFamiliesResponse.status === "fulfilled") {
      setRuleFamilies(Array.isArray(ruleFamiliesResponse.value?.rule_families) ? ruleFamiliesResponse.value.rule_families : []);
    }
    if (unresolvedResponse.status === "fulfilled") {
      setUnresolvedRows(Array.isArray(unresolvedResponse.value?.rows) ? unresolvedResponse.value.rows : []);
    }
    if (coverageResponse.status === "fulfilled") {
      setFamilyCoverageRows(Array.isArray(coverageResponse.value?.rows) ? coverageResponse.value.rows : []);
    }
    if (reviewQueueResponse.status === "fulfilled") {
      setReviewQueueRows(Array.isArray(reviewQueueResponse.value?.criteria) ? reviewQueueResponse.value.criteria : []);
    }
  }, []);

  const loadTrialBundle = useCallback(async (trialId) => {
    if (!trialId) return;
    setCriteriaLoading(true);
    try {
      const [trialResponse, criteriaResponse] = await Promise.all([
        APIGetClinicalTrial(trialId),
        APIGetClinicalTrialCriteria(trialId),
      ]);
      const nextTrial = trialResponse?.trial || null;
      const nextCriteria = normalizeCriteriaRows(criteriaResponse?.criteria || []);
      setSelectedTrial(nextTrial);
      setCriteriaRows(nextCriteria);
      setSelectedCriterionId((current) => {
        if (current && nextCriteria.some((row) => row?._id === current)) return current;
        const preferredRow = nextCriteria.find((row) => row?.node_type === "leaf") || nextCriteria[0] || null;
        return preferredRow?._id || "";
      });
    } catch (error) {
      setIngestError(normalizeClinicalTrialsError(error, "Failed to load trial transparency bundle."));
      setSelectedTrial(null);
      setCriteriaRows([]);
    } finally {
      setCriteriaLoading(false);
    }
  }, []);

  const loadCriteriaCluster = useCallback(async (fingerprint) => {
    if (!fingerprint) {
      setClusterRows([]);
      return;
    }
    try {
      const response = await APIListClinicalTrialCriteria({
        fingerprint,
        limit: 40,
      });
      setClusterRows(Array.isArray(response?.criteria) ? response.criteria : []);
    } catch (error) {
      setClusterRows([]);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      setAnalyticsLoading(true);
      try {
        const healthy = await refreshServiceHealth();
        if (healthy) {
          await refreshWorkbenchSummary();
        }
      } finally {
        if (!cancelled) {
          setAnalyticsLoading(false);
        }
      }
    };
    initialize();
    return () => {
      cancelled = true;
    };
  }, [refreshServiceHealth, refreshWorkbenchSummary]);

  useEffect(() => {
    if (!selectedTrialId) return;
    loadTrialBundle(selectedTrialId);
  }, [loadTrialBundle, selectedTrialId]);

  useEffect(() => {
    if (!selectedFingerprint) return;
    loadCriteriaCluster(selectedFingerprint);
  }, [loadCriteriaCluster, selectedFingerprint]);

  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;
    let timeoutId = null;

    const poll = async () => {
      setRunLoading(true);
      try {
        const response = await APIGetClinicalTrialRun(activeRunId);
        if (cancelled) return;
        const nextRun = response?.run || null;
        setActiveRun(nextRun);
        const trialIdFromRun = nextRun?.artifacts?.trial_id || nextRun?.scope?.nct_id || "";
        if (trialIdFromRun) {
          setSelectedTrialId((current) => current || trialIdFromRun);
        }
        if ((nextRun?.status || "") && TERMINAL_RUN_STATUSES.has(String(nextRun.status))) {
          await refreshWorkbenchSummary();
          if (trialIdFromRun) {
            await loadTrialBundle(trialIdFromRun);
          }
          return;
        }
      } catch (error) {
        if (!cancelled) {
          setIngestError(normalizeClinicalTrialsError(error, "Failed to refresh ingest run."));
        }
      } finally {
        if (!cancelled) {
          setRunLoading(false);
          timeoutId = window.setTimeout(poll, 1200);
        }
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [activeRunId, loadTrialBundle, refreshWorkbenchSummary]);

  useEffect(() => {
    if (!selectedCriterion) return;
    const selectedByCriterion =
      selectedCriterion?.compiler?.selected_family_id
      || selectedCriterion?.compiler?.candidate_families?.[0]?.family_id
      || "";
    if (selectedByCriterion) {
      setSelectedFamilyId((current) => current || selectedByCriterion);
    }
    setReviewForm(normalizeReviewFormFromCriterion(selectedCriterion));
  }, [selectedCriterion]);

  useEffect(() => {
    if (!activeObjectTab?.id) return;
    if (activeObjectTab.id !== objectExplorerTab) {
      setObjectExplorerTab(activeObjectTab.id);
    }
  }, [activeObjectTab, objectExplorerTab]);

  useEffect(() => {
    if (wizardStep === "contract" && objectExplorerTab !== "trial") {
      setObjectExplorerTab("trial");
    }
  }, [wizardStep, objectExplorerTab, selectedTrialId]);

  const buildWizardStepTone = (stepId) => {
    if (stepId === "ingest") {
      return recentRuns.length > 0 || searchResults.length > 0 ? "pass" : "neutral";
    }
    if (stepId === "run") {
      return activeRun ? buildStatusTone(activeRun?.status) : "neutral";
    }
    if (stepId === "contract") {
      return selectedTrial ? buildStatusTone(selectedTrial?.compile?.status) : "neutral";
    }
    if (stepId === "review") {
      if (reviewQueueRows.length > 0 || unresolvedRows.length > 0) return "review";
      return selectedTrial ? "pass" : "neutral";
    }
    if (stepId === "inspect") {
      return criteriaRows.length > 0 || ruleFamilies.length > 0 ? "pass" : "neutral";
    }
    return "neutral";
  };

  const buildWizardStepMeta = (stepId) => {
    if (stepId === "ingest") return recentRuns.length > 0 ? `${formatCount(recentRuns.length)} runs available` : "Queue the first ingest";
    if (stepId === "run") return activeRun ? titleCase(activeRun?.status) : "No run selected";
    if (stepId === "contract") return selectedTrial ? getSelectedTrialContractLabel(selectedTrial) : "No trial loaded";
    if (stepId === "review") {
      const pendingCount = reviewQueueRows.length + unresolvedRows.length;
      return pendingCount > 0 ? `${formatCount(pendingCount)} review signals` : "No review backlog";
    }
    if (stepId === "inspect") return selectedCriterion ? "Criterion selected" : "Advanced tooling";
    return "";
  };

  function getSelectedTrialContractLabel(trial) {
    return titleCase(trial?.compile?.status || "stored");
  }

  const wizardHeadline = useMemo(() => {
    if (wizardStep === "ingest") {
      return "Start by finding one study, or queue a small guided corpus refresh.";
    }
    if (wizardStep === "run") {
      return activeRun
        ? "This is the live ingest pipeline. Each stage tells you what the system is doing and what artifact it produced."
        : "Choose a recent run to see the pipeline explain itself stage by stage.";
    }
    if (wizardStep === "contract") {
      return selectedTrial
        ? "This is the actual ingested trial object and the contract the matcher will use downstream."
        : "Select an ingested trial to inspect its stored document and generated contract.";
    }
    if (wizardStep === "review") {
      return "This is not failure debt. It is the safety layer where the compiler parks ambiguous criteria instead of hallucinating certainty.";
    }
    return "Use the advanced inspector when you want raw criteria, compiler decisions, and rule family details.";
  }, [wizardStep, activeRun, selectedTrial]);

  const handleSearchInputChange = (field, value) => {
    setSearchForm((previous) => ({
      ...previous,
      [field]: value,
    }));
  };

  const runRemoteSearch = async () => {
    if (!serviceHealth.ok && !serviceHealth.checking) {
      setIngestError(serviceHealth.message);
      return;
    }
    setSearchLoading(true);
    setIngestError("");
    try {
      const payload = {
        condition: searchForm.condition,
        phase: searchForm.nctId ? undefined : searchForm.phase || undefined,
        sponsor: searchForm.sponsor || undefined,
        nct_id: searchForm.nctId || undefined,
        page_size: 12,
        max_pages: 4,
      };
      const response = await APISearchClinicalTrials(payload);
      setSearchResults(Array.isArray(response?.results) ? response.results : []);
      setWizardStep("ingest");
    } catch (error) {
      setSearchResults([]);
      setIngestError(normalizeClinicalTrialsError(error, "Failed to search ClinicalTrials.gov."));
    } finally {
      setSearchLoading(false);
    }
  };

  const handleIngestTrial = async (row) => {
    const trialId = row?.nct_id;
    if (!trialId) return;
    if (!serviceHealth.ok && !serviceHealth.checking) {
      setIngestError(serviceHealth.message);
      return;
    }
    setIngestingTrialId(trialId);
    setIngestError("");
    try {
      const response = await APIIngestClinicalTrial({
        nct_id: trialId,
        trigger: "gui_manual_demo",
        condition: searchForm.condition,
        phase: searchForm.phase || undefined,
        sponsor: searchForm.sponsor || undefined,
      });
      setActiveRunId(response?.run_id || "");
      setSelectedTrialId(trialId);
      setActiveRun(null);
      setWizardStep("run");
      await refreshWorkbenchSummary();
    } catch (error) {
      setIngestError(normalizeClinicalTrialsError(error, "Failed to start clinical trial ingest."));
    } finally {
      setIngestingTrialId("");
    }
  };

  const handleBulkIngest = async () => {
    if (!serviceHealth.ok && !serviceHealth.checking) {
      setIngestError(serviceHealth.message);
      return;
    }
    setBulkIngestLoading(true);
    setIngestError("");
    try {
      const response = await APIBulkIngestClinicalTrials({
        condition: searchForm.condition,
        phase: searchForm.phase || undefined,
        sponsor: searchForm.sponsor || undefined,
        page_size: 25,
        max_pages: 5,
        limit: 50,
        trigger: "gui_bulk_corpus",
      });
      setActiveRunId(response?.run_id || "");
      setActiveRun(null);
      setWizardStep("run");
      await refreshWorkbenchSummary();
    } catch (error) {
      setIngestError(normalizeClinicalTrialsError(error, "Failed to start bulk ingest."));
    } finally {
      setBulkIngestLoading(false);
    }
  };

  const handleReviewFormChange = (field, value) => {
    setReviewForm((previous) => ({
      ...previous,
      [field]: value,
    }));
  };

  const handleApplyReview = async () => {
    if (!selectedCriterion?._id) return;
    setReviewSubmitting(true);
    setIngestError("");
    try {
      const parsedRule = reviewForm.finalCompiledRuleText?.trim()
        ? JSON.parse(reviewForm.finalCompiledRuleText)
        : null;
      const response = await APIApplyClinicalTrialReview({
        criterion_id: selectedCriterion._id,
        decision: reviewForm.decision,
        final_family_id: reviewForm.finalFamilyId || undefined,
        reviewer: reviewForm.reviewer || undefined,
        notes: reviewForm.notes || undefined,
        final_compiled_rule: reviewForm.decision === "not_patient_rule" ? undefined : parsedRule || undefined,
        recompile_target: reviewForm.recompileTarget,
        recompile_reason: "gui_review_apply",
      });
      setActiveRunId(response?.run_id || "");
      setWizardStep("run");
      await refreshWorkbenchSummary();
    } catch (error) {
      setIngestError(normalizeClinicalTrialsError(error, "Failed to apply review."));
    } finally {
      setReviewSubmitting(false);
    }
  };

  const handleRecompileTrial = async () => {
    if (!selectedTrialId) return;
    setIngestError("");
    try {
      const response = await APIRecompileClinicalTrial(selectedTrialId, {
        trigger: "gui_trial_recompile",
        reason: "manual_gui_recompile",
      });
      setActiveRunId(response?.run_id || "");
      setActiveRun(null);
      setWizardStep("run");
      await refreshWorkbenchSummary();
    } catch (error) {
      setIngestError(normalizeClinicalTrialsError(error, "Failed to recompile trial."));
    }
  };

  const handleRecompileFamily = async () => {
    const familyId = selectedFamily?.family_id || reviewForm.finalFamilyId;
    if (!familyId) return;
    setIngestError("");
    try {
      const response = await APIRecompileClinicalTrialFamily(familyId, {
        trigger: "gui_family_recompile",
        reason: "manual_gui_recompile",
      });
      setActiveRunId(response?.run_id || "");
      setActiveRun(null);
      setWizardStep("run");
      await refreshWorkbenchSummary();
    } catch (error) {
      setIngestError(normalizeClinicalTrialsError(error, "Failed to recompile family."));
    }
  };

  return (
    <div className="ct-workbench">
      <section className="pharma-card ct-workbench-intro">
        <div className="ct-workbench-intro-copy">
          <span className="ct-workbench-eyebrow">Deterministic Ingestion Workbench</span>
          <h2>Guide one study from source fetch to match-ready contract</h2>
          <p className="pharma-muted">
            This frontstage version keeps the happy path simple: find a study, watch the ingest pipeline explain itself,
            inspect the stored trial object, and only then open the safe-review and advanced compiler tools.
          </p>
        </div>
        <div className="ct-workbench-chip-row">
          <span className={`pharma-evidence-chip ${serviceHealth.ok ? "pass" : serviceHealth.checking ? "neutral" : "blocked"}`}>
            {serviceHealth.checking ? "Checking service" : serviceHealth.ok ? "Service ready" : "Service blocked"}
          </span>
          <span className={`pharma-evidence-chip ${activeRun ? buildStatusTone(activeRun?.status) : "neutral"}`}>
            {activeRun ? titleCase(activeRun?.status) : "No active ingest"}
          </span>
          <span className="pharma-evidence-chip neutral">{formatCount(recentRuns.length)} recent runs</span>
          <span className="pharma-evidence-chip neutral">{formatCount(recentTrials.length)} ingested trials</span>
          <span className="pharma-evidence-chip neutral">{formatCount(unresolvedRows.length)} reusable review patterns</span>
          <span className="pharma-evidence-chip neutral">{formatCount(reviewQueueRows.length)} manual adjudications</span>
        </div>
      </section>

      {ingestError ? <div className="pharma-error">{ingestError}</div> : null}

      <section className="pharma-card">
        <div className="ct-section-head compact">
          <div>
            <span className="ct-workbench-eyebrow">Runtime</span>
            <h3>Clinical Trials Service Readiness</h3>
          </div>
          <button
            type="button"
            className="pharma-btn secondary"
            onClick={async () => {
              setAnalyticsLoading(true);
              try {
                const healthy = await refreshServiceHealth();
                if (healthy) {
                  await refreshWorkbenchSummary();
                }
              } finally {
                setAnalyticsLoading(false);
              }
            }}
            disabled={analyticsLoading}
          >
            {analyticsLoading ? "Refreshing..." : "Refresh Service"}
          </button>
        </div>
        <div className="ct-service-status-card">
          <div className={`ct-service-status-pill ${serviceHealth.ok ? "pass" : serviceHealth.checking ? "neutral" : "blocked"}`}>
            {serviceHealth.checking ? "Checking localhost:8002" : serviceHealth.ok ? "Clinical trials sidecar is live" : "Clinical trials sidecar is offline"}
          </div>
          <p className="pharma-muted">{serviceHealth.message}</p>
          {!serviceHealth.ok ? (
            <div className="ct-workbench-empty">
              Start the local clinical-trials runtime with <code>{LOCAL_CLINICAL_TRIALS_START_COMMAND}</code>, then click <strong>Refresh Service</strong>.
            </div>
          ) : null}
        </div>
      </section>

      <section className="pharma-card ct-wizard-shell">
        <div className="ct-section-head compact">
          <div>
            <span className="ct-workbench-eyebrow">Guided Flow</span>
            <h3>Wizard Navigation</h3>
          </div>
          <div className="ct-inline-meta">
            {selectedTrialId ? <span className="pharma-evidence-chip neutral">{selectedTrialId}</span> : null}
            {activeRunId ? <span className="pharma-evidence-chip neutral">{activeRunId}</span> : null}
          </div>
        </div>
        <div className="ct-wizard-tabs" role="tablist" aria-label="Clinical trial ingestion wizard">
          {WORKBENCH_WIZARD_STEPS.map((step) => (
            <button
              key={step.id}
              type="button"
              role="tab"
              aria-selected={wizardStep === step.id}
              className={`ct-wizard-tab ${wizardStep === step.id ? "active" : ""}`}
              onClick={() => setWizardStep(step.id)}
            >
              <span className="ct-wizard-tab-step">{step.stepLabel}</span>
              <strong>{step.title}</strong>
              <p>{step.description}</p>
              <span className={`pharma-evidence-chip ${buildWizardStepTone(step.id)}`}>{buildWizardStepMeta(step.id)}</span>
            </button>
          ))}
        </div>
        <div className="ct-wizard-context">
          <strong>{WORKBENCH_WIZARD_STEPS.find((step) => step.id === wizardStep)?.title || "Guided Flow"}</strong>
          <p className="pharma-muted">{wizardHeadline}</p>
        </div>
      </section>

      {wizardStep === "ingest" ? (
        <div className="ct-workbench-grid ct-workbench-grid-primary">
          <section className="pharma-card">
            <div className="ct-section-head">
              <div>
                <span className="ct-workbench-eyebrow">Step 1</span>
                <h3>Find A Trial And Queue Ingest</h3>
              </div>
              <div className="ct-header-actions">
                <button type="button" className="pharma-btn secondary" onClick={runRemoteSearch} disabled={searchLoading || !serviceHealth.ok}>
                  {searchLoading ? "Searching..." : "Search ClinicalTrials.gov"}
                </button>
                <button type="button" className="pharma-btn primary" onClick={handleBulkIngest} disabled={bulkIngestLoading || !serviceHealth.ok}>
                  {bulkIngestLoading ? "Queueing..." : "Bulk Ingest Query"}
                </button>
              </div>
            </div>
            <div className="ct-step-story">
              <strong>What this step does</strong>
              <p className="pharma-muted">
                Search the public registry, choose one study, and queue an immutable ingest run. Once queued, the wizard moves to the pipeline step.
              </p>
            </div>
            <div className="ct-search-grid">
              <div className="pharma-field">
                <label>Condition</label>
                <input value={searchForm.condition} onChange={(event) => handleSearchInputChange("condition", event.target.value)} />
              </div>
              <div className="pharma-field">
                <label>Phase</label>
                <input value={searchForm.phase} onChange={(event) => handleSearchInputChange("phase", event.target.value)} placeholder="PHASE3" />
              </div>
              <div className="pharma-field">
                <label>Sponsor</label>
                <input value={searchForm.sponsor} onChange={(event) => handleSearchInputChange("sponsor", event.target.value)} placeholder="Pfizer" />
              </div>
              <div className="pharma-field">
                <label>NCT ID</label>
                <input value={searchForm.nctId} onChange={(event) => handleSearchInputChange("nctId", event.target.value)} placeholder="NCT05654623" />
              </div>
            </div>
            {searchResults.length > 0 ? (
              <div className="ct-results-table-wrap">
                <table className="ct-results-table">
                  <thead>
                    <tr>
                      <th>NCT ID</th>
                      <th>Trial</th>
                      <th>Phase</th>
                      <th>Status</th>
                      <th>Updated</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {searchResults.map((row) => (
                      <tr key={row.nct_id}>
                        <td>{row.nct_id}</td>
                        <td>
                          <strong>{row.brief_title || row.title}</strong>
                          <span>{Array.isArray(row.conditions) ? row.conditions.join(", ") : "-"}</span>
                        </td>
                        <td>{row.phase || "-"}</td>
                        <td>{titleCase(row.status || "-")}</td>
                        <td>{formatDateTime(row.source_last_updated)}</td>
                        <td>
                          <button
                            type="button"
                            className="pharma-btn primary"
                            onClick={() => handleIngestTrial(row)}
                            disabled={ingestingTrialId === row.nct_id}
                          >
                            {ingestingTrialId === row.nct_id ? "Queueing..." : "Ingest"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="ct-workbench-empty">
                Search results will appear here. Use `breast cancer` plus `PHASE3` to build the deterministic pilot corpus.
              </div>
            )}
          </section>

          <section className="pharma-card">
            <div className="ct-section-head">
              <div>
                <span className="ct-workbench-eyebrow">Step 1 Support</span>
                <h3>Recent Runs</h3>
              </div>
              <button type="button" className="pharma-btn secondary" onClick={refreshWorkbenchSummary} disabled={analyticsLoading}>
                {analyticsLoading ? "Refreshing..." : "Refresh"}
              </button>
            </div>
            <div className="ct-step-story">
              <strong>Use recent runs as a shortcut</strong>
              <p className="pharma-muted">
                If you already ingested a study, pick the run here and jump straight to the live pipeline or the stored contract.
              </p>
            </div>
            <div className="ct-run-list">
              {recentRuns.length > 0 ? recentRuns.map((run) => (
                <button
                  type="button"
                  key={run._id}
                  className={`ct-run-list-item ${activeRunId === run._id ? "active" : ""}`}
                  onClick={() => {
                    setActiveRunId(run._id);
                    setWizardStep("run");
                    if (run?.artifacts?.trial_id) {
                      setSelectedTrialId(run.artifacts.trial_id);
                    }
                  }}
                >
                  <div className="ct-run-list-head">
                    <strong>{run._id}</strong>
                    <span className={`pharma-evidence-chip ${buildStatusTone(run?.status)}`}>{titleCase(run?.status)}</span>
                  </div>
                  <span>{run?.scope?.nct_id || "-"}</span>
                  <span>{formatDateTime(run?.started_at)}</span>
                </button>
              )) : (
                <div className="ct-workbench-empty">No ingestion runs yet.</div>
              )}
            </div>
          </section>
        </div>
      ) : null}

      {wizardStep === "run" ? (
        <section className="pharma-card">
          <div className="ct-section-head">
            <div>
              <span className="ct-workbench-eyebrow">Step 2</span>
              <h3>Watch The Pipeline</h3>
            </div>
            <div className="ct-inline-meta">
              <span className={`pharma-evidence-chip ${activeRun ? buildStatusTone(activeRun?.status) : "neutral"}`}>
                {activeRun ? titleCase(activeRun?.status) : "No run selected"}
              </span>
              {activeRun?.artifacts?.version_id ? (
                <span className="pharma-evidence-chip neutral">{activeRun.artifacts.version_id}</span>
              ) : null}
              {selectedTrialId ? (
                <button type="button" className="pharma-btn secondary" onClick={() => setWizardStep("contract")}>
                  Open Stored Contract
                </button>
              ) : null}
            </div>
          </div>
          <div className="ct-step-story">
            <strong>What this step does</strong>
            <p className="pharma-muted">
              This is the execution console. It shows where the source study was frozen, how the structure was parsed, what the compiler decided, and when the match contract became available.
            </p>
          </div>
          {activeRun ? (
            <>
              <div className="ct-run-progress-head">
                <div>
                  <strong>{activeRun?.scope?.nct_id || "-"}</strong>
                  <span>{activeRun?.run_type || "ingest_trial"}</span>
                </div>
                <div className="ct-run-progress-value">
                  <strong>{overallRunPercent}%</strong>
                  <span>{runLoading ? "Refreshing..." : "Live progress"}</span>
                </div>
              </div>
              <div className="ct-overall-progress-track">
                <div className={`ct-overall-progress-fill ${buildStatusTone(activeRun?.status)}`} style={{ width: `${overallRunPercent}%` }} />
              </div>
            </>
          ) : (
            <div className="ct-workbench-empty" style={{ marginBottom: 14 }}>
              Choose a recent run to populate live metrics. The stage guide below still explains what the pipeline does.
            </div>
          )}
          <div className="ct-stage-grid">
            {pipelineStageGuide.map((guide) => (
              <article key={guide.stageName} className={`ct-stage-card ${String(guide.stage?.status || "pending").toLowerCase()}`}>
                <div className="ct-stage-card-head">
                  <strong>{guide.label}</strong>
                  <span className={`pharma-evidence-chip ${buildStatusTone(guide.stage?.status)}`}>
                    {guide.stage ? titleCase(guide.stage?.status || "pending") : "Not started"}
                  </span>
                </div>
                <p className="ct-stage-copy">{guide.description}</p>
                <div className="ct-stage-track">
                  <div className={`ct-stage-fill ${buildStatusTone(guide.stage?.status)}`} style={{ width: `${buildStagePercent(guide.stage)}%` }} />
                </div>
                <div className="ct-stage-times">
                  <span>{formatDateTime(guide.stage?.started_at)}</span>
                  <span>{formatDateTime(guide.stage?.finished_at)}</span>
                </div>
                <StageMetricList metrics={guide.stage?.metrics} />
              </article>
            ))}
          </div>
          <div className="ct-run-console-grid">
            <div className="ct-run-console-panel">
              <h4>Live Events</h4>
              <div className="ct-event-log">
                {activeRun && (activeRun?.events_tail || []).length > 0 ? (activeRun.events_tail || []).slice().reverse().map((event, index) => (
                  <div key={`${event.ts}-${index}`} className={`ct-event-row ${String(event?.level || "").toLowerCase()}`}>
                    <span>{formatDateTime(event?.ts)}</span>
                    <strong>{String(event?.level || "info").toUpperCase()}</strong>
                    <p>{event?.message || "-"}</p>
                  </div>
                )) : (
                  <div className="ct-workbench-empty">Run events will appear here as the pipeline advances.</div>
                )}
              </div>
            </div>
            <div className="ct-run-console-panel">
              <h4>Run Scope</h4>
              <div className="pharma-kv-grid">
                <div><span>Run ID</span><strong>{activeRun?._id || "-"}</strong></div>
                <div><span>Trigger</span><strong>{activeRun?.trigger || "-"}</strong></div>
                <div><span>Trial ID</span><strong>{activeRun?.artifacts?.trial_id || activeRun?.scope?.nct_id || "-"}</strong></div>
                <div><span>Version ID</span><strong>{activeRun?.artifacts?.version_id || "-"}</strong></div>
              </div>
              {activeRun?.error ? (
                <pre className="ct-json-block">{formatJson(activeRun.error)}</pre>
              ) : (
                <p className="pharma-muted">
                  Select a completed run to see the exact operational record and the immutable version identifier it produced.
                </p>
              )}
            </div>
          </div>
        </section>
      ) : null}

      {wizardStep === "contract" ? (
        <div className="ct-workbench-grid ct-workbench-grid-secondary">
          <section className="pharma-card">
            <div className="ct-section-head">
              <div>
                <span className="ct-workbench-eyebrow">Step 3</span>
                <h3>Stored Trial And Match Contract</h3>
              </div>
              <div className="ct-header-actions">
                <button type="button" className="pharma-btn secondary" onClick={handleRecompileTrial} disabled={!selectedTrialId}>
                  Recompile Trial
                </button>
                <button type="button" className="pharma-btn secondary" onClick={() => setWizardStep("review")} disabled={!selectedTrial}>
                  Open Safe Review
                </button>
              </div>
            </div>
            <div className="ct-step-story">
              <strong>What this step does</strong>
              <p className="pharma-muted">
                This is the business-facing handoff. It shows the stored trial document, the generated contract, and whether the contract is ready to drive matching.
              </p>
            </div>
            {selectedTrial ? (
              <>
                <div className="ct-step-story">
                  <strong>Loaded study</strong>
                  <p className="pharma-muted">
                    This handoff step is now scoped to one concrete study only: <strong>{selectedTrial._id}</strong>. To switch studies, go back to
                    {" "}
                    <strong>Find And Queue</strong>
                    {" "}
                    or pick a different run in
                    {" "}
                    <strong>Watch The Pipeline</strong>.
                  </p>
                  <div className="ct-header-actions" style={{ marginTop: 10 }}>
                    <button type="button" className="pharma-btn secondary" onClick={() => setWizardStep("ingest")}>
                      Change Study
                    </button>
                    {activeRunId ? (
                      <button type="button" className="pharma-btn secondary" onClick={() => setWizardStep("run")}>
                        Open Pipeline Run
                      </button>
                    ) : null}
                  </div>
                </div>
                <h4 className="ct-trial-title">{selectedTrial.title || selectedTrial.brief_title || selectedTrial._id}</h4>
                <div className="ct-next-actions">
                  <p className="pharma-muted">
                    After ingestion, choose whether to use this canonical contract for one trial to many patients or open the patient-to-trials flow with the ingested catalog already in scope.
                  </p>
                  <div className="ct-next-action-grid">
                    <button
                      type="button"
                      className="pharma-btn primary"
                      onClick={() => onContinueToTrialScreening?.(selectedTrial)}
                      disabled={!onContinueToTrialScreening || !selectedTrialRunnable}
                    >
                      One Trial → Many Patients
                    </button>
                    <button
                      type="button"
                      className="pharma-btn secondary"
                      onClick={() => onContinueToPatientMatching?.(selectedTrial)}
                      disabled={!onContinueToPatientMatching || !selectedTrialIdValue}
                    >
                      One Patient → Many Trials
                    </button>
                  </div>
                  {!selectedTrialRunnable ? (
                    <p className="pharma-muted">
                      Trial-to-patients stays disabled until this contract becomes runtime-ready.
                    </p>
                  ) : null}
                </div>
                <div className="pharma-kv-grid">
                  <div><span>Compile status</span><strong>{titleCase(selectedTrial?.compile?.status || "-")}</strong></div>
                  <div><span>Review pending</span><strong>{formatCount(selectedTrialReviewPendingCount)}</strong></div>
                  <div><span>Deterministic rules</span><strong>{formatCount(selectedTrialDeterministicCount)}</strong></div>
                  <div><span>Manual rules</span><strong>{formatCount(selectedTrial?.criteria_summary?.manual_review_rules || 0)}</strong></div>
                </div>
                <div className="ct-contract-sections">
                  <div>
                    <h4>Structured Eligibility</h4>
                    <pre className="ct-json-block">{formatJson(selectedTrial?.structured_eligibility)}</pre>
                  </div>
                  <div>
                    <h4>Match Contract</h4>
                    <pre className="ct-json-block">{formatJson(selectedTrial?.match_contract)}</pre>
                  </div>
                </div>
              </>
            ) : (
              <div className="ct-workbench-empty">Pick an ingested trial to inspect its current contract.</div>
            )}
          </section>

          <section className="pharma-card">
            <div className="ct-section-head">
              <div>
                <span className="ct-workbench-eyebrow">Full Object</span>
                <h3>Stored Object Explorer</h3>
              </div>
              {activeObjectTab?.payload ? (
                <span className="pharma-evidence-chip pass">Exact stored payload visible</span>
              ) : null}
            </div>
            <div className="ct-step-story">
              <strong>What you are seeing</strong>
              <p className="pharma-muted">
                This is the full study-level payload currently driving the demonstrator. Use it to inspect the complete stored trial document or the operational run record behind it.
              </p>
            </div>
            <div className="ct-object-tabs" role="tablist" aria-label="Stored object explorer">
              {availableObjectTabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={activeObjectTab?.id === tab.id}
                  className={`ct-object-tab ${activeObjectTab?.id === tab.id ? "active" : ""}`}
                  onClick={() => setObjectExplorerTab(tab.id)}
                  disabled={!tab.payload}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <p className="pharma-muted">{activeObjectTab?.description || "Select an object to inspect."}</p>
            <pre className="ct-json-block ct-json-block-tall">{formatJson(activeObjectTab?.payload || {})}</pre>
          </section>
        </div>
      ) : null}

      {wizardStep === "review" ? (
        <>
          <section className="pharma-card">
            <div className="ct-section-head">
              <div>
                <span className="ct-workbench-eyebrow">Step 4</span>
                <h3>Safe Review And Improvement</h3>
              </div>
              <button type="button" className="pharma-btn secondary" onClick={() => setWizardStep("inspect")}>
                Open Advanced Inspector
              </button>
            </div>
            <div className="ct-step-story">
              <strong>Why this exists</strong>
              <p className="pharma-muted">
                These panels should not read as weakness. They show the exact places where the compiler behaved safely by refusing to over-claim certainty. Repeated patterns here are opportunities to expand deterministic coverage later.
              </p>
            </div>
            <div className="pharma-kv-grid">
              <div><span>Selected trial</span><strong>{selectedTrialId || "-"}</strong></div>
              <div><span>Pending review on selected trial</span><strong>{formatCount(selectedTrialReviewPendingCount)}</strong></div>
              <div><span>Reusable review patterns</span><strong>{formatCount(unresolvedRows.length)}</strong></div>
              <div><span>Manual adjudications</span><strong>{formatCount(reviewQueueRows.length)}</strong></div>
            </div>
          </section>

          <div className="ct-workbench-grid ct-workbench-grid-secondary">
            <section className="pharma-card">
              <div className="ct-analytics-block">
                <h4>Family Coverage</h4>
                <p className="pharma-muted">
                  This is where we see which rule families are already paying off across many trials and where more reviewer effort would increase deterministic coverage.
                </p>
                <FamilyCoverageBars rows={familyCoverageRows} onSelectFamily={(familyId) => {
                  setSelectedFamilyId(familyId);
                  setWizardStep("inspect");
                }} />
              </div>
            </section>

            <section className="pharma-card">
              <div className="ct-analytics-block">
                <h4>Reusable Review Patterns</h4>
                <p className="pharma-muted">
                  These are repeated criterion shapes worth productizing into stronger rule families over time.
                </p>
                {unresolvedRows.length > 0 ? (
                  <div className="ct-unresolved-table-wrap">
                    <table className="ct-unresolved-table">
                      <thead>
                        <tr>
                          <th>Count</th>
                          <th>Example Pattern</th>
                          <th>Section</th>
                          <th>Trials</th>
                        </tr>
                      </thead>
                      <tbody>
                        {unresolvedRows.map((row) => (
                          <tr
                            key={row.fingerprint}
                            className={selectedFingerprint === row.fingerprint ? "active" : ""}
                            onClick={() => setSelectedFingerprint(row.fingerprint)}
                          >
                            <td>{formatCount(row.count)}</td>
                            <td>{truncateText(row.sample_text || row.fingerprint, 180)}</td>
                            <td>{titleCase(row.section)}</td>
                            <td>{formatCount(row.trial_count)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="ct-workbench-empty">No reusable review patterns are currently clustered.</div>
                )}
                {selectedFingerprint ? (
                  <div className="ct-cluster-detail">
                    <h5>Examples In This Pattern</h5>
                    {clusterRows.length > 0 ? clusterRows.slice(0, 6).map((row) => (
                      <button
                        type="button"
                        key={row._id}
                        className="ct-cluster-row"
                        onClick={() => {
                          setSelectedTrialId(row.trial_id);
                          setSelectedCriterionId(row._id);
                          setWizardStep("inspect");
                        }}
                      >
                        <strong>{row.trial_id}</strong>
                        <span>{truncateText(row.raw?.text || "-", 220)}</span>
                      </button>
                    )) : (
                      <div className="ct-workbench-empty">Loading cluster examples...</div>
                    )}
                  </div>
                ) : null}
              </div>
              <div className="ct-analytics-block">
                <h4>Manual Adjudication Queue</h4>
                <p className="pharma-muted">
                  These are the patient-impacting criteria the compiler intentionally held back for human judgment.
                </p>
                {reviewQueueRows.length > 0 ? (
                  <div className="ct-review-queue">
                    {reviewQueueRows.slice(0, 8).map((row) => (
                      <button
                        type="button"
                        key={row._id}
                        className="ct-cluster-row"
                        onClick={() => {
                          setSelectedTrialId(row.trial_id);
                          setSelectedCriterionId(row._id);
                          setWizardStep("inspect");
                        }}
                      >
                        <strong>{row.trial_id}</strong>
                        <span>{truncateText(row.raw?.text || "-", 220)}</span>
                        <span>{summarizeReviewReason(row)}</span>
                        <span>
                          Weight {formatCount(row.analytics?.patient_relevance_weight || 0)}
                          {" • "}
                          {titleCase(row.section)}
                        </span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="ct-workbench-empty">No manual adjudications are pending right now.</div>
                )}
              </div>
            </section>
          </div>
        </>
      ) : null}

      {wizardStep === "inspect" ? (
        <div className="ct-workbench-grid ct-workbench-grid-secondary">
          <section className="pharma-card">
            <div className="ct-section-head">
              <div>
                <span className="ct-workbench-eyebrow">Step 5</span>
                <h3>Criteria Inspector</h3>
              </div>
              {criteriaLoading ? <span className="pharma-evidence-chip neutral">Loading...</span> : null}
            </div>
            <div className="ct-step-story">
              <strong>Use this when you want the exact compiler evidence</strong>
              <p className="pharma-muted">
                This is the deepest inspection layer: raw criterion text, normalization, compiler choice, review state, and reviewer override tools.
              </p>
            </div>
            {criteriaRows.length > 0 ? (
              <div className="ct-inspector-grid">
                <div className="ct-criteria-list">
                  {sortedCriteriaRows.map((row) => (
                    <button
                      type="button"
                      key={row._id}
                      className={`ct-criteria-row ${selectedCriterion?._id === row._id ? "active" : ""}`}
                      onClick={() => setSelectedCriterionId(row._id)}
                    >
                      <div className="ct-criteria-row-head">
                        <span>{titleCase(row.section)}</span>
                        <span className={`pharma-evidence-chip ${buildStatusTone(row?.compiler?.parse_status)}`}>
                          {titleCase(row?.compiler?.parse_status || row?.node_type)}
                        </span>
                      </div>
                      <strong>{truncateText(row.raw?.text || "-", 180)}</strong>
                      <span>Depth {row.depth ?? 0} • {row.node_type}</span>
                    </button>
                  ))}
                </div>
                <div className="ct-criteria-detail">
                  {selectedCriterion ? (
                    <>
                      <div className="ct-detail-block">
                        <h4>Raw And Context</h4>
                        <div className="ct-detail-summary">
                          <div><span>Node</span><strong>{selectedCriterion._id}</strong></div>
                          <div><span>Parent</span><strong>{selectedCriterion.parent_node_id || "-"}</strong></div>
                        </div>
                        <pre className="ct-json-block">{formatJson({
                          raw_text: selectedCriterion?.raw?.text,
                          ancestor_headers: selectedCriterion?.ancestor_headers || [],
                          source_kind: selectedCriterion?.source_kind,
                          path: selectedCriterion?.path || [],
                        })}</pre>
                      </div>
                      <div className="ct-detail-block">
                        <h4>Normalized</h4>
                        <pre className="ct-json-block">{formatJson(selectedCriterion?.normalized)}</pre>
                      </div>
                      <div className="ct-detail-block">
                        <h4>Compiler Decision</h4>
                        <pre className="ct-json-block">{formatJson(selectedCriterion?.compiler)}</pre>
                      </div>
                      <div className="ct-detail-block">
                        <h4>Review State</h4>
                        <pre className="ct-json-block">{formatJson(selectedCriterion?.review)}</pre>
                      </div>
                      <div className="ct-detail-block">
                        <div className="ct-section-head compact">
                          <div>
                            <h4>Apply Review And Recompile</h4>
                          </div>
                        </div>
                        <div className="ct-review-form-grid">
                          <div className="pharma-field">
                            <label>Decision</label>
                            <select value={reviewForm.decision} onChange={(event) => handleReviewFormChange("decision", event.target.value)}>
                              <option value="confirm_family">Confirm Family</option>
                              <option value="manual_review_clinical">Keep Manual Clinical</option>
                              <option value="not_patient_rule">Mark Not Patient Rule</option>
                            </select>
                          </div>
                          <div className="pharma-field">
                            <label>Final Family</label>
                            <input value={reviewForm.finalFamilyId} onChange={(event) => handleReviewFormChange("finalFamilyId", event.target.value)} />
                          </div>
                          <div className="pharma-field">
                            <label>Reviewer</label>
                            <input value={reviewForm.reviewer} onChange={(event) => handleReviewFormChange("reviewer", event.target.value)} />
                          </div>
                          <div className="pharma-field">
                            <label>Recompile Target</label>
                            <select value={reviewForm.recompileTarget} onChange={(event) => handleReviewFormChange("recompileTarget", event.target.value)}>
                              <option value="trial">Trial</option>
                              <option value="family">Family</option>
                              <option value="none">None</option>
                            </select>
                          </div>
                        </div>
                        <div className="pharma-field">
                          <label>Review Notes</label>
                          <textarea rows={3} value={reviewForm.notes} onChange={(event) => handleReviewFormChange("notes", event.target.value)} />
                        </div>
                        <div className="pharma-field">
                          <label>Final Compiled Rule JSON</label>
                          <textarea
                            rows={10}
                            value={reviewForm.finalCompiledRuleText}
                            onChange={(event) => handleReviewFormChange("finalCompiledRuleText", event.target.value)}
                            placeholder='{"kind":"inclusion","family":"performance.ecog.max","logic":{"predicate":"performance.ecog","operator":"<=","value":1},"required_patient_features":["performance.ecog"],"prefilter_eligible":true}'
                          />
                        </div>
                        <div className="ct-header-actions">
                          <button type="button" className="pharma-btn primary" onClick={handleApplyReview} disabled={reviewSubmitting}>
                            {reviewSubmitting ? "Applying..." : "Apply Review"}
                          </button>
                          <button type="button" className="pharma-btn secondary" onClick={() => setReviewForm(normalizeReviewFormFromCriterion(selectedCriterion))}>
                            Reset Review Form
                          </button>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="ct-workbench-empty">Select a criterion to inspect raw, normalized, and compiled views.</div>
                  )}
                </div>
              </div>
            ) : (
              <div className="ct-workbench-empty">Criteria will populate once a trial has been ingested.</div>
            )}
          </section>

          <section className="pharma-card">
            <div className="ct-section-head">
              <div>
                <span className="ct-workbench-eyebrow">Rule Registry</span>
                <h3>Rule Family Registry</h3>
              </div>
              <button type="button" className="pharma-btn secondary" onClick={handleRecompileFamily} disabled={!selectedFamily?.family_id}>
                Recompile Family
              </button>
            </div>
            <div className="ct-step-story">
              <strong>Use this when you want the reusable compiler pattern</strong>
              <p className="pharma-muted">
                Family rows describe the reusable rule templates behind many criteria. This is where a new curated family can improve coverage across multiple trials at once.
              </p>
            </div>
            {ruleFamilies.length > 0 ? (
              <div className="ct-family-grid">
                <div className="ct-family-list">
                  {ruleFamilies.map((family) => (
                    <button
                      type="button"
                      key={family._id}
                      className={`ct-family-row ${selectedFamily?.family_id === family.family_id ? "active" : ""}`}
                      onClick={() => setSelectedFamilyId(family.family_id)}
                    >
                      <strong>{family.family_id}</strong>
                      <span>{titleCase(family.status)}</span>
                    </button>
                  ))}
                </div>
                <div className="ct-family-detail">
                  {selectedFamily ? (
                    <>
                      <div className="ct-inline-meta">
                        <span className={`pharma-evidence-chip ${selectedFamily.status === "active" ? "pass" : "review"}`}>
                          {titleCase(selectedFamily.status)}
                        </span>
                        <span className="pharma-evidence-chip neutral">v{selectedFamily.version}</span>
                      </div>
                      <p className="ct-family-description">{selectedFamily.description}</p>
                      <div className="ct-detail-block">
                        <h4>Triggers</h4>
                        <pre className="ct-json-block">{formatJson(selectedFamily.triggers)}</pre>
                      </div>
                      <div className="ct-detail-block">
                        <h4>Compiler Contract</h4>
                        <pre className="ct-json-block">{formatJson(selectedFamily.compiler_contract)}</pre>
                      </div>
                      <div className="ct-detail-block">
                        <h4>Examples</h4>
                        <pre className="ct-json-block">{formatJson(selectedFamily.examples)}</pre>
                      </div>
                    </>
                  ) : (
                    <div className="ct-workbench-empty">Select a family to inspect trigger rules and usage patterns.</div>
                  )}
                </div>
              </div>
            ) : (
              <div className="ct-workbench-empty">Rule families load from the clinical-trials service startup seed.</div>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}
