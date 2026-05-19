import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ReadinessScorecard,
  SectionDivider,
  SpaceHeader,
  StatusChip,
} from "../../../components/PharmaDesignSystem/PharmaDesignSystem";
import CriterionTreeView from "../../../components/PharmaMatching/CriterionTreeView";
import { AgenticProgressTicker } from "../../../components/PharmaMatching";
import {
  APIBulkIngestClinicalTrials,
  APICompileClinicalTrialPlayground,
  APIGetClinicalTrial,
  APIGetClinicalTrialCanonicalTree,
  APIGetClinicalTrialRun,
  APIGetClinicalTrialsHealth,
  APIIngestClinicalTrial,
  APIListClinicalTrialRuns,
  APIListClinicalTrials,
  APISearchClinicalTrials,
} from "../../../services/APIService";

const DEFAULT_CORPUS_FORM = {
  condition: "breast cancer",
  pageSize: 12,
  maxPages: 4,
  limit: 25,
};
const DEFAULT_DISCOVERY_CORPUS_FORM = {
  condition: "cancer",
  pageSize: 12,
  maxPages: 4,
  limit: 25,
};

const SINGLE_INGEST_STEPS = [
  "Fetching one study from ClinicalTrials.gov",
  "Parsing eligibility structure",
  "Compiling criterion families",
  "Materializing canonical contract",
];

const BULK_INGEST_STEPS = [
  "Searching ClinicalTrials.gov corpus",
  "Refreshing local corpus membership",
  "Compiling canonical contracts",
  "Summarizing stored coverage",
];

const TRIAL_SERVICE_UNAVAILABLE_COPY = "Clinical trials sidecar unavailable. Start npm --prefix app run start:clinical-trials-service, then reload this studio.";

const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 45;

const safe = (fn) => fn().catch(() => null);

const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

const normalizeTrialPayload = (payload) => payload?.trial || payload || null;
const normalizeCanonicalTreePayload = (payload) => payload?.canonical_tree || payload?.tree || payload || null;
const normalizeRunPayload = (payload) => payload?.run || payload || null;

const normalizeStatuses = (value) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const diseaseAreaFromCondition = (condition) =>
  String(condition || "").toLowerCase().includes("breast") ? "breast_cancer" : undefined;

const getTrialId = (trial) => {
  if (typeof trial === "string") {
    return trial.trim();
  }
  return String(trial?._id || trial?.nct_id || trial?.nctId || trial?.trialId || "").trim();
};

const getTrialTitle = (trial) =>
  trial?.title || trial?.brief_title || trial?.briefTitle || getTrialId(trial) || "Untitled trial";

const getTrialPhase = (trial) => trial?.phase || trial?.phases || "-";
const getTrialStatus = (trial) => trial?.status || trial?.overall_status || trial?.overallStatus || "-";

const isRunComplete = (run) => {
  const status = String(run?.status || "").toLowerCase();
  return ["completed", "complete", "done", "failed", "error", "completed_with_review", "skipped_unchanged"].includes(status);
};

const formatRunLabel = (run) => {
  const runType = String(run?.run_type || run?.runType || "").trim();
  if (!runType) return "Run";
  return runType.replace(/_/g, " ");
};

const formatCollectionDate = (value) => {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
};

const getRunTrialCount = (run) => {
  const trialIds = run?.artifacts?.trial_ids || run?.scope?.trial_ids || [];
  if (Array.isArray(trialIds) && trialIds.length > 0) {
    return trialIds.length;
  }
  return run?.scope?.nct_id || run?.artifacts?.trial_id ? 1 : 0;
};

function flattenSectionSourceNodes(treePayload, section) {
  const roots = Array.isArray(treePayload?.roots) ? treePayload.roots : [];
  const root = roots.find((item) => String(item?.section || "").toLowerCase() === section);
  if (!root) return [];

  const rows = [];
  const visit = (nodes) => {
    (nodes || []).forEach((node) => {
      if (node?.nodeType !== "section_root" && node?.sourceKind !== "structured_field" && node?.source?.text) {
        rows.push(node);
      }
      visit(node?.children || []);
    });
  };
  visit(root.children || []);
  return rows.sort((a, b) => {
    const ordinalDelta = Number(a?.sourceOrdinal || 0) - Number(b?.sourceOrdinal || 0);
    if (ordinalDelta !== 0) return ordinalDelta;
    return String(a?.nodeId || "").localeCompare(String(b?.nodeId || ""));
  });
}

function parseCorpusFormToSearchPayload(form) {
  return {
    condition: String(form.condition || "").trim() || "breast cancer",
    page_size: Math.max(1, Number(form.pageSize || 12)),
    max_pages: Math.max(1, Number(form.maxPages || 4)),
  };
}

function parseCorpusFormToBulkPayload(form) {
  return {
    ...parseCorpusFormToSearchPayload(form),
    limit: Math.max(1, Number(form.limit || form.pageSize || 25)),
    trigger: "gui_bulk_corpus",
  };
}

function buildDefaultCorpusForm(demoLens) {
  return demoLens === "discovery" ? DEFAULT_DISCOVERY_CORPUS_FORM : DEFAULT_CORPUS_FORM;
}

function formatReadinessCopy(readiness) {
  const state = String(readiness?.runtimeState || "").trim().toLowerCase();
  if (state === "runtime_ready_deterministic") return "Runnable · deterministic";
  if (state === "runtime_ready_with_manual") return "Runnable · manual review needed";
  if (state === "blocked_unsupported") return "Blocked · unsupported";
  if (readiness?.runnable) return "Runnable";
  return "Blocked";
}

function collectFamilyIds(treePayload) {
  const ids = new Set();
  const visit = (nodes) => {
    (nodes || []).forEach((node) => {
      const familyId = String(node?.compiler?.familyId || "").trim();
      if (familyId) ids.add(familyId);
      visit(node?.children || []);
    });
  };
  visit(treePayload?.roots || []);
  return Array.from(ids);
}

function countCriteriaNodes(treePayload) {
  let count = 0;
  const visit = (nodes) => {
    (nodes || []).forEach((node) => {
      if (String(node?.nodeType || "") !== "section_root") {
        count += 1;
      }
      visit(node?.children || []);
    });
  };
  visit(treePayload?.roots || []);
  return count;
}

function TrialIngestionStudio({
  navigateSpace,
  selectedTrialId,
  setSelectedTrialId,
  demoLens = "breast_oncology_ai",
}) {
  const defaultCorpusForm = useMemo(() => buildDefaultCorpusForm(demoLens), [demoLens]);
  const [mode, setMode] = useState("ingest");
  const [studioMode, setStudioMode] = useState("demo");
  const [serviceHealthy, setServiceHealthy] = useState(null);

  const [corpusForm, setCorpusForm] = useState(defaultCorpusForm);
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState(null);

  const [storedTrials, setStoredTrials] = useState([]);
  const [storedTotalCount, setStoredTotalCount] = useState(0);
  const [storedLoading, setStoredLoading] = useState(false);
  const [storedError, setStoredError] = useState(null);

  const [recentRuns, setRecentRuns] = useState([]);

  const [selectedTrial, setSelectedTrial] = useState(null);
  const [treePayload, setTreePayload] = useState(null);
  const [trialLoading, setTrialLoading] = useState(false);

  const [activeRun, setActiveRun] = useState(null);
  const [singleIngestLoading, setSingleIngestLoading] = useState(false);
  const [singleIngestError, setSingleIngestError] = useState(null);
  const [bulkLoading, setBulkLoading] = useState(false);
  const [bulkError, setBulkError] = useState(null);

  const [playgroundText, setPlaygroundText] = useState("");
  const [playgroundSection, setPlaygroundSection] = useState("inclusion");
  const [playgroundHeadersText, setPlaygroundHeadersText] = useState("");
  const [playgroundResult, setPlaygroundResult] = useState(null);
  const [playgroundLoading, setPlaygroundLoading] = useState(false);
  const [playgroundError, setPlaygroundError] = useState(null);
  const [playgroundTreeTab, setPlaygroundTreeTab] = useState("source");
  const [canvasTab, setCanvasTab] = useState("source");
  const [selectedSourceNodeId, setSelectedSourceNodeId] = useState("");

  const loadRecentRuns = useCallback(async () => {
    const response = await APIListClinicalTrialRuns({ limit: 6 });
    const rows = Array.isArray(response?.runs) ? response.runs : Array.isArray(response) ? response : [];
    setRecentRuns(rows);
    return rows;
  }, []);

  const loadStoredCorpus = useCallback(async (formOverride = defaultCorpusForm) => {
    const nextForm = formOverride || defaultCorpusForm;
    setStoredLoading(true);
    setStoredError(null);
    try {
      const response = await APIListClinicalTrials({
        disease_area: diseaseAreaFromCondition(nextForm.condition),
        limit: 40,
      });
      const rows = Array.isArray(response?.trials) ? response.trials : Array.isArray(response) ? response : [];
      setStoredTrials(rows);
      setStoredTotalCount(Number(response?.totalCount || rows.length || 0));
      return rows;
    } catch (error) {
      setStoredError(error?.message || "Failed to load stored corpus");
      setStoredTrials([]);
      setStoredTotalCount(0);
      return [];
    } finally {
      setStoredLoading(false);
    }
  }, [defaultCorpusForm]);

  const searchRemoteCorpus = useCallback(async (formOverride = defaultCorpusForm) => {
    const nextForm = formOverride || defaultCorpusForm;
    setSearchLoading(true);
    setSearchError(null);
    try {
      const response = await APISearchClinicalTrials(parseCorpusFormToSearchPayload(nextForm));
      const rows = Array.isArray(response?.results) ? response.results : Array.isArray(response) ? response : [];
      setSearchResults(rows);
      return rows;
    } catch (error) {
      setSearchError(error?.message || "Failed to query ClinicalTrials.gov");
      setSearchResults([]);
      return [];
    } finally {
      setSearchLoading(false);
    }
  }, [defaultCorpusForm]);

  const loadTrial = useCallback(async (trialId) => {
    if (!trialId) return;
    setTrialLoading(true);
    try {
      const [trialResponse, treeResponse] = await Promise.all([
        APIGetClinicalTrial(trialId),
        APIGetClinicalTrialCanonicalTree(trialId).catch(() => null),
      ]);
      const normalizedTrial = normalizeTrialPayload(trialResponse);
      const normalizedTree = normalizeCanonicalTreePayload(treeResponse);
      setSelectedTrial(normalizedTrial);
      setTreePayload(normalizedTree);
      if (setSelectedTrialId) {
        setSelectedTrialId(getTrialId(normalizedTrial) || trialId);
      }
    } catch (error) {
      setSelectedTrial(null);
      setTreePayload(null);
    } finally {
      setTrialLoading(false);
    }
  }, [setSelectedTrialId]);

  const pollRun = useCallback(async (runId) => {
    let polls = 0;
    while (polls < MAX_POLLS) {
      polls += 1;
      const response = await APIGetClinicalTrialRun(runId);
      const run = normalizeRunPayload(response);
      setActiveRun(run);
      if (isRunComplete(run)) {
        return run;
      }
      await sleep(POLL_INTERVAL_MS);
    }
    throw new Error("Timed out while waiting for the clinical-trials run to complete");
  }, []);

  const refreshCorpusPreview = useCallback(async (formOverride = null) => {
    await Promise.all([
      loadStoredCorpus(formOverride),
      searchRemoteCorpus(formOverride),
      loadRecentRuns(),
    ]);
  }, [loadRecentRuns, loadStoredCorpus, searchRemoteCorpus]);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      const health = await safe(() => APIGetClinicalTrialsHealth());
      if (cancelled) return;
      setServiceHealthy(Boolean(health?.ok || health?.healthy || health?.status === "ok"));
      await refreshCorpusPreview(defaultCorpusForm);
    }
    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [defaultCorpusForm, refreshCorpusPreview]);

  useEffect(() => {
    setCorpusForm(buildDefaultCorpusForm(demoLens));
  }, [demoLens]);

  useEffect(() => {
    if (serviceHealthy !== true) return;
    refreshCorpusPreview(defaultCorpusForm);
  }, [defaultCorpusForm, refreshCorpusPreview, serviceHealthy]);

  useEffect(() => {
    if (!selectedTrialId) return;
    loadTrial(selectedTrialId);
  }, [selectedTrialId, loadTrial]);

  const storedTrialMap = useMemo(() => {
    const map = new Map();
    storedTrials.forEach((trial) => {
      const trialId = getTrialId(trial);
      if (trialId) {
        map.set(trialId, trial);
      }
    });
    return map;
  }, [storedTrials]);

  const remoteNewCount = useMemo(
    () => searchResults.filter((trial) => !storedTrialMap.has(getTrialId(trial))).length,
    [searchResults, storedTrialMap]
  );

  const selectedLoadedTrialId = getTrialId(selectedTrial);
  const structuredEligibility = selectedTrial?.structured_eligibility || selectedTrial?.structuredEligibility || {};
  const inclusionNodes = useMemo(() => flattenSectionSourceNodes(treePayload, "inclusion"), [treePayload]);
  const exclusionNodes = useMemo(() => flattenSectionSourceNodes(treePayload, "exclusion"), [treePayload]);
  const allSourceNodes = useMemo(() => [...inclusionNodes, ...exclusionNodes], [inclusionNodes, exclusionNodes]);
  const canonicalSummary = treePayload?.canonicalSummary || {};
  const sourceSummary = treePayload?.sourceSummary || {};
  const runtimeReadiness = selectedTrial?.runtimeReadiness || treePayload?.runtimeReadiness || {};
  const criteriaNodeCount = useMemo(() => countCriteriaNodes(treePayload), [treePayload]);
  const familyIdsUsed = useMemo(() => collectFamilyIds(treePayload), [treePayload]);
  const playgroundCanonicalTree = playgroundResult?.canonicalTree || null;

  useEffect(() => {
    setCanvasTab("source");
  }, [selectedLoadedTrialId, treePayload?.versionId]);

  useEffect(() => {
    if (playgroundCanonicalTree) {
      setPlaygroundTreeTab("source");
    }
  }, [playgroundCanonicalTree, playgroundResult?.selectedFamily]);

  useEffect(() => {
    if (!allSourceNodes.length) {
      setSelectedSourceNodeId("");
      return;
    }
    setSelectedSourceNodeId((current) =>
      allSourceNodes.some((node) => String(node?.nodeId || "") === String(current || ""))
        ? current
        : String(allSourceNodes[0]?.nodeId || "")
    );
  }, [allSourceNodes]);

  useEffect(() => {
    if (mode !== "ingest" || studioMode !== "demo") return;
    if (selectedLoadedTrialId || trialLoading) return;
    const demoTrialId = getTrialId(storedTrials[0]);
    if (demoTrialId) {
      loadTrial(demoTrialId);
    }
  }, [loadTrial, mode, selectedLoadedTrialId, storedTrials, studioMode, trialLoading]);

  const selectedSourceNode = useMemo(() => {
    if (!allSourceNodes.length) return null;
    return allSourceNodes.find((node) => String(node?.nodeId || "") === String(selectedSourceNodeId || "")) || allSourceNodes[0];
  }, [allSourceNodes, selectedSourceNodeId]);

  const contractArtifacts = useMemo(() => ([
    {
      label: "Source Snapshot",
      collection: "trial_versions",
      count: selectedTrial ? 1 : 0,
      identifier: selectedTrial?.current_version_id || "-",
    },
    {
      label: "Criterion Nodes",
      collection: "trial_criteria",
      count: criteriaNodeCount,
      identifier: selectedLoadedTrialId ? `${selectedLoadedTrialId}::*` : "-",
    },
    {
      label: "Published Trial Contract",
      collection: "trials",
      count: selectedTrial ? 1 : 0,
      identifier: selectedLoadedTrialId || "-",
    },
    {
      label: "Rule Families Used",
      collection: "rule_families",
      count: familyIdsUsed.length,
      identifier: familyIdsUsed.slice(0, 2).join(" | ") || "-",
    },
  ]), [criteriaNodeCount, familyIdsUsed, selectedLoadedTrialId, selectedTrial]);

  const handleCorpusFieldChange = useCallback((field, value) => {
    setCorpusForm((current) => ({ ...current, [field]: value }));
  }, []);

  const handleSearchSubmit = useCallback(async (event) => {
    if (event) event.preventDefault();
    if (serviceHealthy !== true) {
      setSearchError(TRIAL_SERVICE_UNAVAILABLE_COPY);
      return;
    }
    await searchRemoteCorpus(corpusForm);
  }, [corpusForm, searchRemoteCorpus, serviceHealthy]);

  const refreshAfterIngest = useCallback(async (preferredTrialId = "") => {
    const rows = await loadStoredCorpus(corpusForm);
    await loadRecentRuns();
    const candidateTrialId =
      preferredTrialId ||
      getTrialId(rows[0]) ||
      getTrialId(searchResults.find((trial) => storedTrialMap.has(getTrialId(trial))));
    if (candidateTrialId) {
      await loadTrial(candidateTrialId);
    }
  }, [corpusForm, loadRecentRuns, loadStoredCorpus, loadTrial, searchResults, storedTrialMap]);

  const handleSingleIngest = useCallback(async (nctId) => {
    if (!nctId) return;
    if (serviceHealthy !== true) {
      setSingleIngestError(TRIAL_SERVICE_UNAVAILABLE_COPY);
      return;
    }
    setSingleIngestLoading(true);
    setSingleIngestError(null);
    setActiveRun(null);
    try {
      const response = await APIIngestClinicalTrial({
        nct_id: nctId,
        condition: corpusForm.condition,
        trigger: "gui_single_ingest",
      });
      const runId = response?.run_id || response?.runId;
      const finalRun = runId ? await pollRun(runId) : null;
      await refreshAfterIngest(getTrialId(finalRun?.artifacts?.trial_id) || nctId);
    } catch (error) {
      setSingleIngestError(error?.message || "Single-trial ingest failed");
    } finally {
      setSingleIngestLoading(false);
    }
  }, [corpusForm.condition, pollRun, refreshAfterIngest, serviceHealthy]);

  const handleBulkRefresh = useCallback(async () => {
    if (serviceHealthy !== true) {
      setBulkError(TRIAL_SERVICE_UNAVAILABLE_COPY);
      return;
    }
    setBulkLoading(true);
    setBulkError(null);
    setActiveRun(null);
    try {
      const response = await APIBulkIngestClinicalTrials(parseCorpusFormToBulkPayload(corpusForm));
      const runId = response?.run_id || response?.runId;
      const finalRun = runId ? await pollRun(runId) : null;
      const processedIds = finalRun?.artifacts?.trial_ids || [];
      const preferredTrialId =
        (Array.isArray(processedIds) && processedIds.length > 0 && processedIds[0]) ||
        getTrialId(searchResults[0]) ||
        "";
      await refreshAfterIngest(preferredTrialId);
      await searchRemoteCorpus(corpusForm);
    } catch (error) {
      setBulkError(error?.message || "Bulk ingest failed");
    } finally {
      setBulkLoading(false);
    }
  }, [corpusForm, pollRun, refreshAfterIngest, searchResults, searchRemoteCorpus, serviceHealthy]);

  const handleOpenStoredOrIngest = useCallback(async (trial) => {
    const nctId = getTrialId(trial);
    if (!nctId) return;
    if (storedTrialMap.has(nctId)) {
      await loadTrial(nctId);
      return;
    }
    await handleSingleIngest(nctId);
  }, [handleSingleIngest, loadTrial, storedTrialMap]);

  const handlePlaygroundCompile = useCallback(async () => {
    if (!playgroundText.trim()) return;
    if (serviceHealthy !== true) {
      setPlaygroundError(TRIAL_SERVICE_UNAVAILABLE_COPY);
      return;
    }
    setPlaygroundLoading(true);
    setPlaygroundError(null);
    setPlaygroundResult(null);
    try {
      const response = await APICompileClinicalTrialPlayground({
        text: playgroundText.trim(),
        section: playgroundSection,
        ancestor_headers: normalizeStatuses(playgroundHeadersText),
      });
      setPlaygroundResult(response?.result || response || null);
    } catch (error) {
      setPlaygroundError(error?.message || "Playground compilation failed");
    } finally {
      setPlaygroundLoading(false);
    }
  }, [playgroundHeadersText, playgroundSection, playgroundText, serviceHealthy]);

  const handleReplaySelectedTrial = useCallback(async () => {
    const trialId = getTrialId(selectedTrial);
    if (!trialId) return;
    await handleSingleIngest(trialId);
  }, [handleSingleIngest, selectedTrial]);

  const activeProgressTitle = bulkLoading
    ? "Refreshing breast-cancer corpus"
    : singleIngestLoading
      ? "Importing one clinical trial"
      : null;
  const activeProgressSteps = bulkLoading ? BULK_INGEST_STEPS : SINGLE_INGEST_STEPS;

  const renderTrialPickerCard = (trial, actionHandler = null) => {
    const trialId = getTrialId(trial);
    const alreadyStored = storedTrialMap.has(trialId);
    const isSelected = String(trialId) === String(selectedLoadedTrialId || "");
    const importBlocked = Boolean(!alreadyStored && actionHandler && serviceHealthy !== true);

    return (
      <button
        key={trialId}
        type="button"
        disabled={importBlocked}
        onClick={() => {
          if (importBlocked) return;
          if (typeof actionHandler === "function") {
            actionHandler(trial);
            return;
          }
          loadTrial(trialId);
        }}
        style={{
          width: "100%",
          textAlign: "left",
          padding: 12,
          borderRadius: 10,
          border: isSelected ? "1px solid var(--pharma-brand, #00684A)" : "1px solid var(--pharma-border, #e2e8f0)",
          background: isSelected ? "var(--pharma-runtime-bg, #ecfdf5)" : "var(--pharma-white, #fff)",
          cursor: importBlocked ? "not-allowed" : "pointer",
          opacity: importBlocked ? 0.72 : 1,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 14, color: "var(--pharma-text, #0f172a)" }}>{getTrialTitle(trial)}</div>
            <div className="pds-muted" style={{ fontSize: 12 }}>
              {[trialId, getTrialPhase(trial), getTrialStatus(trial)].filter(Boolean).join(" | ")}
            </div>
          </div>
          <StatusChip status={alreadyStored ? "pass" : "unknown"} label={alreadyStored ? "In corpus" : "Not yet in corpus"} size="sm" />
        </div>
        <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>
          {alreadyStored
            ? "Open published contract"
            : importBlocked
              ? "Import paused until the clinical trials sidecar is running"
              : `Import from ClinicalTrials.gov · updated ${formatCollectionDate(trial.source_last_updated)}`}
        </div>
      </button>
    );
  };

  const renderCriterionInspector = (node) => {
    if (!node) {
      return (
        <div className="pds-card" style={{ padding: 16 }}>
          <div className="pds-muted" style={{ fontSize: 13 }}>
            Select one criterion to inspect how it becomes a canonical node.
          </div>
        </div>
      );
    }

    return (
      <div
        className="pds-card"
        style={{
          padding: 18,
          border: "1px solid #dbe7ff",
          background: "linear-gradient(180deg, #f8fbff 0%, #ffffff 100%)",
          boxShadow: "0 10px 24px rgba(15, 23, 42, 0.06)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 12,
            paddingBottom: 12,
            borderBottom: "1px solid rgba(59, 130, 246, 0.14)",
          }}
        >
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 10,
              background: "var(--pharma-canonical-bg, #eff6ff)",
              color: "var(--pharma-canonical, #3b82f6)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 800,
              fontSize: 13,
              flexShrink: 0,
            }}
          >
            i
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)" }}>
              Inspector
            </div>
            <div style={{ fontSize: 13, color: "var(--pharma-text-secondary, #475569)", marginTop: 2 }}>
              Selected source criterion and its compiled interpretation
            </div>
          </div>
        </div>

        <div style={{ fontSize: 14, fontWeight: 700, lineHeight: 1.55, color: "var(--pharma-text, #0f172a)" }}>
          {node?.source?.text || node?.label || "-"}
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 12 }}>
          <StatusChip status="neutral" label={node?.sourceRef || "-"} size="sm" />
          <StatusChip status="neutral" label={String(node?.section || "").replace(/_/g, " ")} size="sm" />
          {node?.compiler?.familyId ? <StatusChip status="canonical" label={node.compiler.familyId} size="sm" /> : null}
          {node?.compiler?.state ? <StatusChip status="neutral" label={node.compiler.state.replace(/_/g, " ")} size="sm" /> : null}
          {node?.matchability?.class ? (
            <StatusChip
              status={node.matchability.class === "deterministic" ? "pass" : node.matchability.class === "manual" ? "warn" : "neutral"}
              label={node.matchability.class.replace(/_/g, " ")}
              size="sm"
            />
          ) : null}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 12,
            marginTop: 14,
          }}
        >
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 6 }}>
              Family
            </div>
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid #dbe7ff",
                background: "#ffffff",
                fontSize: 13,
                fontWeight: 700,
                lineHeight: 1.5,
                color: "var(--pharma-text, #0f172a)",
                overflowWrap: "anywhere",
                wordBreak: "break-word",
              }}
            >
              {node?.compiler?.familyId || "-"}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 6 }}>
              Evaluator
            </div>
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid #dbe7ff",
                background: "#ffffff",
                fontSize: 13,
                fontWeight: 700,
                lineHeight: 1.5,
                color: "var(--pharma-text, #0f172a)",
                overflowWrap: "anywhere",
                wordBreak: "break-word",
              }}
            >
              {node?.patientSupport?.evaluator || "-"}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 6 }}>
              Prefilter Eligible
            </div>
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid #e2e8f0",
                background: "#ffffff",
                fontSize: 14,
                fontWeight: 800,
                color: node?.matchability?.prefilterEligible ? "var(--pharma-brand, #00684A)" : "var(--pharma-text, #0f172a)",
              }}
            >
              {node?.matchability?.prefilterEligible ? "Yes" : "No"}
            </div>
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.04em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 6 }}>
              Patient-Impacting
            </div>
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid #e2e8f0",
                background: "#ffffff",
                fontSize: 14,
                fontWeight: 800,
                color: node?.matchability?.patientImpacting ? "var(--pharma-brand, #00684A)" : "var(--pharma-text, #0f172a)",
              }}
            >
              {node?.matchability?.patientImpacting ? "Yes" : "No"}
            </div>
          </div>
        </div>

        {node?.logic?.predicate ? (
          <div
            style={{
              marginTop: 14,
              padding: "10px 12px",
              borderRadius: 10,
              background: "var(--pharma-runtime-bg, #dcfce7)",
              color: "var(--pharma-runtime, #00684A)",
              fontFamily: "monospace",
              fontSize: 12,
              lineHeight: 1.5,
              overflowWrap: "anywhere",
              wordBreak: "break-word",
            }}
          >
            {node.logic.predicate} {node.logic?.comparator || ""} {JSON.stringify(node.logic?.value ?? "")}
          </div>
        ) : null}

        {Array.isArray(node?.source?.ancestorHeaders) && node.source.ancestorHeaders.length > 0 ? (
          <div
            className="pds-muted"
            style={{
              fontSize: 12,
              marginTop: 12,
              padding: "10px 12px",
              borderRadius: 10,
              background: "rgba(148, 163, 184, 0.08)",
              border: "1px solid rgba(148, 163, 184, 0.16)",
              lineHeight: 1.5,
              overflowWrap: "anywhere",
            }}
          >
            <strong style={{ color: "var(--pharma-text, #0f172a)" }}>Context:</strong> {node.source.ancestorHeaders.join(" > ")}
          </div>
        ) : null}
      </div>
    );
  };

  const renderSourceNodeRow = (node) => {
    const isSelected = String(node?.nodeId || "") === String(selectedSourceNode?.nodeId || "");
    return (
      <div key={node.nodeId} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <button
          type="button"
          onClick={() => setSelectedSourceNodeId(String(node.nodeId || ""))}
          style={{
            width: "100%",
            textAlign: "left",
            padding: "10px 12px",
            borderRadius: 10,
            border: isSelected
              ? "1px solid var(--pharma-canonical, #3b82f6)"
              : "1px solid var(--pharma-border, #e2e8f0)",
            background: isSelected
              ? "var(--pharma-canonical-bg, #eff6ff)"
              : "var(--pharma-white, #fff)",
            cursor: "pointer",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--pharma-text, #0f172a)", lineHeight: 1.4 }}>
            {String(node?.source?.text || node?.label || "-").slice(0, 160)}
            {String(node?.source?.text || node?.label || "").length > 160 ? "..." : ""}
          </div>
          <div className="pds-muted" style={{ fontSize: 12, marginTop: 4 }}>
            {node?.sourceRef || "-"}
          </div>
        </button>
        {isSelected ? renderCriterionInspector(node) : null}
      </div>
    );
  };

  const renderContractFactCard = (label, value, options = {}) => {
    const {
      tone = "neutral",
      mono = false,
      wide = false,
    } = options;
    const accentColor =
      tone === "positive"
        ? "var(--pharma-brand, #00684A)"
        : tone === "caution"
          ? "#b45309"
          : "var(--pharma-text, #0f172a)";
    const panelBackground =
      tone === "positive"
        ? "rgba(0, 104, 74, 0.05)"
        : tone === "caution"
          ? "rgba(245, 158, 11, 0.08)"
          : "var(--pharma-bg, #f8fafc)";
    const panelBorder =
      tone === "positive"
        ? "rgba(0, 104, 74, 0.18)"
        : tone === "caution"
          ? "rgba(245, 158, 11, 0.28)"
          : "var(--pharma-border, #e2e8f0)";

    return (
      <div
        style={{
          padding: "14px 16px",
          borderRadius: 14,
          border: `1px solid ${panelBorder}`,
          background: panelBackground,
          minWidth: 0,
          minHeight: 108,
          boxShadow: "0 10px 30px rgba(15, 23, 42, 0.04)",
          gridColumn: wide ? "span 2" : "span 1",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.05em",
            textTransform: "uppercase",
            color: "var(--pharma-text-muted, #94a3b8)",
            marginBottom: 8,
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: mono ? 13 : 16,
            fontWeight: 800,
            lineHeight: 1.55,
            color: accentColor,
            fontFamily: mono ? "monospace" : "inherit",
            overflowWrap: "anywhere",
            wordBreak: "break-word",
            whiteSpace: "normal",
          }}
        >
          {value}
        </div>
      </div>
    );
  };

  return (
    <div className="pds-fade-in">
      <SpaceHeader
        title="Trial Factory"
        subtitle={
          demoLens === "discovery"
            ? "Search more broadly across ClinicalTrials.gov, then decide which studies deserve governed promotion into the deeper AI lane."
            : "Take a breast-oncology trial from ClinicalTrials.gov, turn it into a canonical contract, and publish it as match-ready."
        }
        onBack={() => navigateSpace("landing")}
        backLabel="Workbench"
      />

      <div className="pds-card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              className={`pds-btn ${mode === "ingest" ? "pds-btn--primary" : "pds-btn--secondary"}`}
              type="button"
              onClick={() => setMode("ingest")}
            >
              Import Trial
            </button>
            <button
              className={`pds-btn ${mode === "playground" ? "pds-btn--primary" : "pds-btn--secondary"}`}
              type="button"
              onClick={() => setMode("playground")}
            >
              Parse One Criterion
            </button>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {mode === "ingest" ? (
              <>
                <button
                  className={`pds-btn ${studioMode === "demo" ? "pds-btn--secondary" : "pds-btn--ghost"}`}
                  type="button"
                  onClick={() => setStudioMode("demo")}
                >
                  Demo Mode
                </button>
                <button
                  className={`pds-btn ${studioMode === "live" ? "pds-btn--secondary" : "pds-btn--ghost"}`}
                  type="button"
                  onClick={() => setStudioMode("live")}
                >
                  Live Mode
                </button>
              </>
            ) : null}
            {serviceHealthy === false ? <StatusChip status="fail" label="Service Unavailable" size="sm" /> : null}
            {serviceHealthy === null ? <StatusChip status="unknown" label="Checking Service" size="sm" /> : null}
          </div>
        </div>
        {serviceHealthy === false ? (
          <div className="pds-muted" style={{ fontSize: 12, marginTop: 10 }}>
            Start the sidecar with `npm --prefix app run start:clinical-trials-service`, then re-open this studio.
          </div>
        ) : null}
      </div>

      {(singleIngestLoading || bulkLoading) ? (
        <div style={{ marginBottom: 16 }}>
          <AgenticProgressTicker
            active={true}
            title={activeProgressTitle}
            subtitle={activeRun?.status ? `Status: ${activeRun.status}` : ""}
            steps={activeProgressSteps}
          />
        </div>
      ) : null}

      {mode === "ingest" ? (
        <div style={{ display: "grid", gridTemplateColumns: "320px minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
          <div className="pds-card" style={{ padding: 18 }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 4 }}>
              Step 1
            </div>
            <h3 style={{ fontSize: 18, fontWeight: 800, margin: 0, color: "var(--pharma-text, #0f172a)" }}>
              Select or import a trial
            </h3>
            <div className="pds-muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
              {studioMode === "demo"
                ? "Demo Mode opens a published example by default. Switch to Live Mode when you want to search CT.gov or refresh the corpus."
                : "Live Mode searches ClinicalTrials.gov and imports the latest breast-cancer trials into the canonical corpus."}
            </div>

            <div style={{ marginTop: 14 }}>
              <ReadinessScorecard
                metrics={[
                  { label: "Trials in Canonical Corpus", value: storedTotalCount, tone: storedTotalCount > 0 ? "pass" : "neutral" },
                  { label: "Matching studies found", value: searchResults.length, tone: searchResults.length > 0 ? "pass" : "neutral" },
                  { label: "Not yet in corpus", value: remoteNewCount, tone: remoteNewCount > 0 ? "warn" : "neutral" },
                ]}
              />
            </div>

            {studioMode === "live" ? (
              <>
                <div className="pds-field" style={{ marginTop: 14 }}>
                  <label className="pds-field__label">Condition</label>
                  <input
                    className="pds-input"
                    type="text"
                    value={corpusForm.condition}
                    onChange={(event) => handleCorpusFieldChange("condition", event.target.value)}
                    placeholder="breast cancer"
                  />
                </div>
                <details style={{ marginTop: 10 }}>
                  <summary style={{ fontSize: 12, fontWeight: 600, cursor: "pointer", userSelect: "none" }}>
                    Advanced import options
                  </summary>
                  <div className="pds-kv-grid" style={{ marginTop: 10 }}>
                    <div>
                      <span>Page size</span>
                      <input className="pds-input" type="number" min="1" max="50" value={corpusForm.pageSize} onChange={(event) => handleCorpusFieldChange("pageSize", event.target.value)} style={{ marginTop: 4 }} />
                    </div>
                    <div>
                      <span>Max pages</span>
                      <input className="pds-input" type="number" min="1" max="10" value={corpusForm.maxPages} onChange={(event) => handleCorpusFieldChange("maxPages", event.target.value)} style={{ marginTop: 4 }} />
                    </div>
                    <div>
                      <span>Bulk limit</span>
                      <input className="pds-input" type="number" min="1" max="100" value={corpusForm.limit} onChange={(event) => handleCorpusFieldChange("limit", event.target.value)} style={{ marginTop: 4 }} />
                    </div>
                  </div>
                </details>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                  <button className="pds-btn pds-btn--secondary" type="button" disabled={searchLoading || !corpusForm.condition.trim() || serviceHealthy !== true} onClick={handleSearchSubmit}>
                    {searchLoading ? "Checking..." : "Check CT.gov"}
                  </button>
                  <button className="pds-btn pds-btn--primary" type="button" disabled={bulkLoading || !corpusForm.condition.trim() || serviceHealthy !== true} onClick={handleBulkRefresh}>
                    {bulkLoading ? "Importing..." : "Import Trial Set"}
                  </button>
                </div>
                {searchError ? <div className="pds-error" style={{ marginTop: 10 }}>{searchError}</div> : null}
                {bulkError ? <div className="pds-error" style={{ marginTop: 10 }}>{bulkError}</div> : null}
                {singleIngestError ? <div className="pds-error" style={{ marginTop: 10 }}>{singleIngestError}</div> : null}
              </>
            ) : null}

            {studioMode === "live" && searchResults.length > 0 ? (
              <>
                <SectionDivider label="ClinicalTrials.gov results" />
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {searchResults.slice(0, 6).map((trial) => renderTrialPickerCard(trial, handleOpenStoredOrIngest))}
                </div>
              </>
            ) : null}

            <SectionDivider label="Published trials" />
            {storedError ? <div className="pds-error">{storedError}</div> : null}
            {storedLoading ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span className="pds-spinner" />
                <span className="pds-muted">Loading canonical corpus...</span>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {storedTrials.slice(0, 8).map((trial) => renderTrialPickerCard(trial))}
                {!storedTrials.length ? (
                  <div className="pds-muted" style={{ fontSize: 13 }}>
                    No trials are published in this scope yet.
                  </div>
                ) : null}
              </div>
            )}

            <details style={{ marginTop: 12 }}>
              <summary style={{ fontSize: 12, fontWeight: 600, cursor: "pointer", userSelect: "none" }}>
                Recent ingest runs
              </summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
                {recentRuns.map((run) => (
                  <div key={run?._id || run?.run_id || run?.id} className="pds-card" style={{ padding: "8px 12px", display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <div>
                      <div style={{ fontWeight: 600, fontSize: 13 }}>{formatRunLabel(run)}</div>
                      <div className="pds-muted" style={{ fontSize: 12 }}>{getRunTrialCount(run)} trial(s)</div>
                    </div>
                    <StatusChip status={String(run?.status || "").includes("completed") ? "pass" : "neutral"} label={run?.status || "unknown"} size="sm" />
                  </div>
                ))}
                {!recentRuns.length ? <div className="pds-muted" style={{ fontSize: 13 }}>No recent ingestion runs yet.</div> : null}
              </div>
            </details>
          </div>

          <div className="pds-card" style={{ padding: 18 }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 4 }}>
              Step 2
            </div>
            <h3 style={{ fontSize: 18, fontWeight: 800, margin: 0, color: "var(--pharma-text, #0f172a)" }}>
              Review the transformation
            </h3>
            <div className="pds-muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
              See what came from the source, how it was transformed, and what was published.
            </div>

            {!selectedTrial ? (
              <div style={{ textAlign: "center", padding: "56px 24px" }}>
                <div style={{ fontSize: 15, color: "var(--pharma-text-secondary)", marginBottom: 8 }}>
                  No trial selected
                </div>
                <div className="pds-muted" style={{ fontSize: 13 }}>
                  Pick one trial from the left column to review the source, canonical logic, and published contract.
                </div>
              </div>
            ) : (
              <>
                <div style={{ marginTop: 14, padding: "12px 14px", borderRadius: 10, background: "var(--pharma-bg, #f8fafc)" }}>
                  <div style={{ fontFamily: "monospace", fontSize: 13, marginBottom: 4, color: "var(--pharma-text-secondary)" }}>
                    {selectedLoadedTrialId}
                  </div>
                  <div style={{ fontWeight: 700, fontSize: 15, color: "var(--pharma-text, #0f172a)" }}>{getTrialTitle(selectedTrial)}</div>
                </div>

                <div className="pds-tabs" style={{ marginTop: 14 }} role="tablist" aria-label="Trial transformation tabs">
                  {[
                    { id: "source", label: "Source" },
                    { id: "canonical", label: "Canonical Logic" },
                    { id: "contract", label: "Published Contract" },
                  ].map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      className={`pds-tab ${canvasTab === tab.id ? "pds-tab--active" : ""}`}
                      onClick={() => setCanvasTab(tab.id)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                <div style={{ marginTop: 16 }}>
                  {canvasTab === "source" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      <ReadinessScorecard
                        metrics={[
                          { label: "Source bullets", value: sourceSummary?.sourceBullets ?? 0, tone: "neutral" },
                          { label: "Group headers", value: sourceSummary?.groupHeaders ?? 0, tone: "neutral" },
                          { label: "Structured criteria", value: sourceSummary?.structuredCriteria ?? 0, tone: "neutral" },
                          { label: "Notes", value: sourceSummary?.notes ?? 0, tone: "neutral" },
                        ]}
                      />
                      <div className="pds-kv-grid">
                        <div>
                          <span>Min age</span>
                          <strong>{structuredEligibility?.minimum_age?.value != null ? `${structuredEligibility.minimum_age.value} ${structuredEligibility.minimum_age.unit || ""}`.trim() : "-"}</strong>
                        </div>
                        <div>
                          <span>Max age</span>
                          <strong>{structuredEligibility?.maximum_age?.value != null ? `${structuredEligibility.maximum_age.value} ${structuredEligibility.maximum_age.unit || ""}`.trim() : "-"}</strong>
                        </div>
                        <div>
                          <span>Sex</span>
                          <strong>{structuredEligibility?.sex || "UNKNOWN"}</strong>
                        </div>
                        <div>
                          <span>Healthy volunteers</span>
                          <strong>{String(structuredEligibility?.healthy_volunteers ?? "No")}</strong>
                        </div>
                      </div>
                      <details open>
                        <summary style={{ fontWeight: 700, cursor: "pointer" }}>Inclusion ({inclusionNodes.length})</summary>
                        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
                          {inclusionNodes.length > 0 ? inclusionNodes.map((node) => renderSourceNodeRow(node)) : <div className="pds-muted" style={{ fontSize: 13 }}>No inclusion bullets available.</div>}
                        </div>
                      </details>
                      <details>
                        <summary style={{ fontWeight: 700, cursor: "pointer" }}>Exclusion ({exclusionNodes.length})</summary>
                        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
                          {exclusionNodes.length > 0 ? exclusionNodes.map((node) => renderSourceNodeRow(node)) : <div className="pds-muted" style={{ fontSize: 13 }}>No exclusion bullets available.</div>}
                        </div>
                      </details>
                    </div>
                  ) : null}

                  {canvasTab === "canonical" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                      <div>
                        {trialLoading ? (
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span className="pds-spinner" />
                            <span className="pds-muted">Loading canonical tree...</span>
                          </div>
                        ) : treePayload ? (
                          <CriterionTreeView
                            treePayload={treePayload}
                            activeTab="canonical"
                            showTabs={false}
                            showFilters={true}
                            showSummary={false}
                            selectedNodeId={selectedSourceNode?.nodeId || ""}
                            onNodeSelect={(node) => setSelectedSourceNodeId(String(node?.nodeId || ""))}
                          />
                        ) : (
                          <div className="pds-muted" style={{ fontSize: 13 }}>Canonical logic is not available yet.</div>
                        )}
                      </div>
                      {selectedSourceNode ? renderCriterionInspector(selectedSourceNode) : null}
                    </div>
                  ) : null}

                  {canvasTab === "contract" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                      <ReadinessScorecard
                        metrics={[
                          { label: "Executable predicates", value: canonicalSummary?.executablePredicates ?? 0, tone: "pass" },
                          { label: "Deterministic", value: canonicalSummary?.deterministic ?? 0, tone: "pass" },
                          { label: "Manual", value: canonicalSummary?.manualReview ?? 0, tone: "warn" },
                          { label: "Prefilter eligible", value: canonicalSummary?.prefilterEligible ?? 0, tone: "neutral" },
                        ]}
                      />
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                          gap: 14,
                          alignItems: "stretch",
                        }}
                      >
                        {renderContractFactCard("Compile status", selectedTrial?.compile?.status || "-", {
                          tone: selectedTrial?.compile?.status === "ready_auto" || selectedTrial?.compile?.status === "ready_with_review"
                            ? "positive"
                            : "neutral",
                        })}
                        {renderContractFactCard("Version id", selectedTrial?.current_version_id || "-", {
                          mono: true,
                          wide: true,
                        })}
                        {renderContractFactCard("Match readiness", formatReadinessCopy(runtimeReadiness), {
                          tone: runtimeReadiness?.runnable ? "positive" : "caution",
                          wide: true,
                        })}
                        {renderContractFactCard("Review pending", selectedTrial?.review_snapshot?.pending_count ?? 0)}
                        {renderContractFactCard("Criteria nodes", criteriaNodeCount)}
                        {renderContractFactCard("Rule families used", familyIdsUsed.length)}
                      </div>
                    </div>
                  ) : null}
                </div>
              </>
            )}

            {/* ── Inline action bar (was third column) ── */}
            {selectedTrial ? (
              <div style={{
                marginTop: 20, padding: "14px 18px", borderRadius: 12,
                background: "var(--pharma-bg, #f8fafc)",
                border: "1px solid var(--pharma-border, #e2e8f0)",
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: 16, flexWrap: "wrap",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flex: 1 }}>
                  <StatusChip status={runtimeReadiness?.runnable ? "pass" : "fail"} label={formatReadinessCopy(runtimeReadiness)} size="sm" />
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--pharma-text, #0f172a)" }}>
                    {canonicalSummary?.deterministic ?? 0} deterministic · {canonicalSummary?.manualReview ?? 0} manual · {contractArtifacts.length} artifacts stored
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                  <button
                    className="pds-btn pds-btn--primary"
                    type="button"
                    onClick={() => navigateSpace("matching", {
                      trialId: getTrialId(selectedTrial),
                      direction: "trial_to_patients",
                      launchSource: "trial_ingestion",
                    })}
                    disabled={!runtimeReadiness?.runnable}
                  >
                    Open in Matching Studio
                  </button>
                  <button className="pds-btn pds-btn--secondary" type="button" onClick={handleReplaySelectedTrial} disabled={singleIngestLoading}>
                    Replay
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="pds-card" style={{ padding: 20 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 4 }}>
            Parse One Criterion
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 800, margin: 0, color: "var(--pharma-text, #0f172a)" }}>
            Curation-only playground
          </h3>
          <div className="pds-muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
            Paste one criterion and inspect how it would be normalized and classified without writing anything to the database.
          </div>

          <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "320px minmax(0, 1fr)", gap: 16 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <StatusChip status="canonical" label="No database write" size="sm" />
              <div className="pds-field">
                <label className="pds-field__label">Criterion text</label>
                <textarea
                  className="pds-input"
                  rows={8}
                  value={playgroundText}
                  onChange={(event) => setPlaygroundText(event.target.value)}
                  placeholder="Paste one eligibility bullet here..."
                  style={{ resize: "vertical", fontFamily: "monospace", fontSize: 13 }}
                />
              </div>
              <div className="pds-kv-grid">
                <div>
                  <span>Section</span>
                  <select className="pds-input" value={playgroundSection} onChange={(event) => setPlaygroundSection(event.target.value)} style={{ marginTop: 6 }}>
                    <option value="inclusion">Inclusion</option>
                    <option value="exclusion">Exclusion</option>
                  </select>
                </div>
                <div>
                  <span>Context headers</span>
                  <input className="pds-input" type="text" value={playgroundHeadersText} onChange={(event) => setPlaygroundHeadersText(event.target.value)} placeholder="e.g. Prior treatment with" style={{ marginTop: 6 }} />
                </div>
              </div>
              <button className="pds-btn pds-btn--primary" type="button" disabled={playgroundLoading || !playgroundText.trim() || serviceHealthy !== true} onClick={handlePlaygroundCompile}>
                {playgroundLoading ? "Compiling..." : "Compile Criterion"}
              </button>
              {playgroundError ? <div className="pds-error">{playgroundError}</div> : null}
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {!playgroundResult ? (
                <div className="pds-muted" style={{ fontSize: 13 }}>
                  Compile one criterion to see the family shortlist, predicate, and canonical tree.
                </div>
              ) : (
                <>
                  <ReadinessScorecard
                    metrics={[
                      { label: "Compiler State", value: playgroundResult?.compilerState || "-", tone: "neutral" },
                      { label: "Matchability", value: playgroundResult?.matchability || "-", tone: playgroundResult?.matchability === "deterministic" ? "pass" : "warn" },
                      { label: "Selected Family", value: playgroundResult?.selectedFamily || "None", tone: playgroundResult?.selectedFamily ? "pass" : "neutral" },
                      { label: "Prefilter Eligible", value: playgroundResult?.prefilterEligible ? "Yes" : "No", tone: playgroundResult?.prefilterEligible ? "pass" : "neutral" },
                    ]}
                  />
                  <div className="pds-kv-grid">
                    <div>
                      <span>Section</span>
                      <strong>{playgroundResult?.section || playgroundSection}</strong>
                    </div>
                    <div>
                      <span>Evaluator</span>
                      <strong>{playgroundResult?.selectedEvaluator || "-"}</strong>
                    </div>
                    <div>
                      <span>Review Status</span>
                      <strong>{playgroundResult?.reviewStatus || "-"}</strong>
                    </div>
                    <div>
                      <span>Database Write</span>
                      <strong>{playgroundResult?.noDatabaseWrite ? "No" : "Yes"}</strong>
                    </div>
                  </div>
                  <CriterionTreeView
                    treePayload={playgroundResult.canonicalTree}
                    activeTab={playgroundTreeTab}
                    onTabChange={setPlaygroundTreeTab}
                    showTabs={true}
                    showFilters={true}
                    showSummary={true}
                  />
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default TrialIngestionStudio;
