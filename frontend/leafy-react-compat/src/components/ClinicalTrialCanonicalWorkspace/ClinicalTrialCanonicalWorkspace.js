import React, { useEffect, useMemo, useState } from "react";

const TABS = [
  { id: "source", label: "Source" },
  { id: "canonical", label: "Canonical Logic" },
  { id: "runtime", label: "Runtime" },
];

const FILTER_DEFS = [
  { id: "deterministicOnly", label: "Deterministic only" },
  { id: "patientImpactingOnly", label: "Patient-impacting only" },
  { id: "unresolvedOnly", label: "Unresolved only" },
  { id: "prefilterEligibleOnly", label: "Prefilter eligible only" },
  { id: "blockersOnly", label: "Blockers only" },
  { id: "reviewedOnly", label: "Reviewed only" },
];

const DEFAULT_FILTERS = FILTER_DEFS.reduce((acc, filterDef) => ({ ...acc, [filterDef.id]: false }), {});

const STATUS_CLASS_MAP = {
  candidate: "likely",
  excluded: "ineligible",
  needs_more_data: "review",
  manual_review_required: "review",
  needs_identity_resolution: "review",
  met: "pass",
  not_met: "blocked",
  unknown: "review",
  blocked_by_linkage_conflict: "blocked",
  fillable_by_questionnaire: "review",
};

const STATUS_LABEL_MAP = {
  auto_compiled: "Auto compiled",
  reviewed: "Reviewed",
  partial_parse: "Partial parse",
  source_only: "Source only",
  not_patient_rule: "Not patient rule",
  deterministic: "Deterministic",
  manual: "Manual",
  unsupported: "Unsupported",
  questionnaire_fillable: "Questionnaire-fillable",
  prefilter_eligible: "Prefilter eligible",
  patient_impacting: "Patient-impacting",
  source: "Source",
  canonical: "Canonical Logic",
  runtime: "Runtime",
  group: "Group",
  predicate: "Predicate",
  synthetic: "Synthetic",
  note: "Note",
  context: "Context",
  section_root: "Section",
};

const BLOCKER_STATUSES = new Set([
  "not_met",
  "unknown",
  "manual_review_required",
  "blocked_by_linkage_conflict",
  "fillable_by_questionnaire",
]);

const formatLabel = (value) => STATUS_LABEL_MAP[String(value || "").trim().toLowerCase()] || String(value || "-").replaceAll("_", " ");

const formatRuntimeChip = (value) => ({
  label: formatLabel(value),
  className: STATUS_CLASS_MAP[String(value || "").trim().toLowerCase()] || "neutral",
});

const summarizeRuntime = (children = [], runtimeRow = null) => {
  const summary = {
    met: 0,
    not_met: 0,
    unknown: 0,
    manual_review_required: 0,
    blocked_by_linkage_conflict: 0,
    fillable_by_questionnaire: 0,
    not_patient_rule: 0,
  };

  children.forEach((child) => {
    const childSummary = child.runtimeSummary || {};
    Object.keys(summary).forEach((key) => {
      summary[key] += Number(childSummary[key] || 0);
    });
  });

  const runtimeStatus = String(runtimeRow?.status || "").trim().toLowerCase();
  if (runtimeStatus && Object.prototype.hasOwnProperty.call(summary, runtimeStatus)) {
    summary[runtimeStatus] += 1;
  }
  return summary;
};

const buildRuntimeRowMap = (runtimeResult) => {
  const map = new Map();
  const rows = Array.isArray(runtimeResult?.criteria) ? runtimeResult.criteria : [];
  rows.forEach((row) => {
    const criterionId = String(row?.criterionId || "").trim();
    if (criterionId) {
      map.set(criterionId, row);
    }
  });
  return map;
};

const annotateRuntime = (node, runtimeMap) => {
  const children = Array.isArray(node?.children) ? node.children.map((child) => annotateRuntime(child, runtimeMap)) : [];
  const runtimeRow = runtimeMap.get(String(node?.nodeId || "")) || null;
  return {
    ...node,
    children,
    runtime: runtimeRow,
    runtimeSummary: summarizeRuntime(children, runtimeRow),
  };
};

const nodeMatchesFilters = (node, filters, runtimeAvailable) => {
  if (!node) return false;
  const nodeType = String(node?.nodeType || "");
  if (nodeType === "section_root") return true;

  if (filters.deterministicOnly && node?.matchability?.class !== "deterministic") return false;
  if (filters.patientImpactingOnly && !node?.matchability?.patientImpacting) return false;
  if (
    filters.unresolvedOnly
    && !(
      ["manual", "unsupported"].includes(node?.matchability?.class)
      || ["partial_parse", "source_only"].includes(node?.compiler?.state)
      || node?.review?.status === "pending"
    )
  ) {
    return false;
  }
  if (filters.prefilterEligibleOnly && !node?.matchability?.prefilterEligible) return false;
  if (filters.reviewedOnly && node?.review?.status !== "reviewed") return false;
  if (filters.blockersOnly) {
    if (runtimeAvailable) {
      const runtimeStatus = String(node?.runtime?.status || "").trim().toLowerCase();
      const runtimeSummary = node?.runtimeSummary || {};
      const hasRuntimeBlocker = BLOCKER_STATUSES.has(runtimeStatus)
        || Number(runtimeSummary.not_met || 0) > 0
        || Number(runtimeSummary.unknown || 0) > 0
        || Number(runtimeSummary.manual_review_required || 0) > 0
        || Number(runtimeSummary.blocked_by_linkage_conflict || 0) > 0
        || Number(runtimeSummary.fillable_by_questionnaire || 0) > 0;
      if (!hasRuntimeBlocker) return false;
    } else if (!node?.matchability?.runtimeBlocker) {
      return false;
    }
  }

  return true;
};

const filterTree = (node, filters, runtimeAvailable) => {
  const children = Array.isArray(node?.children)
    ? node.children.map((child) => filterTree(child, filters, runtimeAvailable)).filter(Boolean)
    : [];
  const selfMatches = nodeMatchesFilters(node, filters, runtimeAvailable);

  if (String(node?.nodeType || "") === "section_root") {
    return { ...node, children };
  }
  if (children.length > 0) {
    return { ...node, children };
  }
  return selfMatches ? { ...node, children } : null;
};

function RuntimeSummaryChips({ summary }) {
  const chips = [
    ["met", "Met"],
    ["not_met", "Not met"],
    ["unknown", "Unknown"],
    ["manual_review_required", "Manual"],
    ["blocked_by_linkage_conflict", "Conflict"],
    ["fillable_by_questionnaire", "Questionnaire"],
  ];
  return (
    <div className="pharma-evidence-chip-row">
      {chips
        .filter(([key]) => Number(summary?.[key] || 0) > 0)
        .map(([key, label]) => (
          <span key={key} className={`pharma-evidence-chip ${formatRuntimeChip(key).className}`}>
            {label}: {Number(summary?.[key] || 0)}
          </span>
        ))}
    </div>
  );
}

function TreeNode({ node, tab, depth = 0 }) {
  const runtimeStatus = String(node?.runtime?.status || "").trim().toLowerCase();
  const runtimeChip = runtimeStatus ? formatRuntimeChip(runtimeStatus) : null;
  const hasChildren = Array.isArray(node?.children) && node.children.length > 0;

  return (
    <div className={`ct-tree-node ${tab}`} style={{ "--ct-depth": depth }}>
      <div className="ct-tree-node-card">
        <div className="ct-tree-node-head">
          <div>
            <div className="ct-tree-node-title">{node?.label || node?.source?.text || node?.sourceRef || "-"}</div>
            <div className="pharma-evidence-chip-row">
              <span className="pharma-evidence-chip neutral">{formatLabel(node?.section)}</span>
              <span className="pharma-evidence-chip neutral">{formatLabel(node?.nodeType)}</span>
              <span className="pharma-evidence-chip neutral">{node?.sourceRef || "-"}</span>
              {tab !== "source" ? (
                <>
                  <span className="pharma-evidence-chip neutral">{formatLabel(node?.compiler?.state)}</span>
                  <span className={`pharma-evidence-chip ${node?.matchability?.class === "deterministic" ? "pass" : node?.matchability?.runtimeBlocker ? "blocked" : "neutral"}`}>
                    {formatLabel(node?.matchability?.class)}
                  </span>
                  {node?.matchability?.prefilterEligible ? <span className="pharma-evidence-chip pass">Prefilter eligible</span> : null}
                  {node?.matchability?.questionnaireFillable ? <span className="pharma-evidence-chip review">Questionnaire-fillable</span> : null}
                  {node?.review?.status === "reviewed" ? <span className="pharma-evidence-chip pass">Reviewed</span> : null}
                </>
              ) : null}
              {tab === "runtime" && runtimeChip ? (
                <span className={`pharma-evidence-chip ${runtimeChip.className}`}>{runtimeChip.label}</span>
              ) : null}
            </div>
          </div>
          {tab === "runtime" && hasChildren ? <RuntimeSummaryChips summary={node?.runtimeSummary} /> : null}
        </div>

        <div className="ct-tree-node-body">
          {tab === "source" ? (
            <>
              <div className="ct-tree-node-line">{node?.source?.text || "-"}</div>
              {Array.isArray(node?.source?.ancestorHeaders) && node.source.ancestorHeaders.length > 0 ? (
                <div className="ct-tree-node-subtle">Context: {node.source.ancestorHeaders.join(" > ")}</div>
              ) : null}
              {node?.source?.sourcePath ? <div className="ct-tree-node-subtle">Source path: {node.source.sourcePath}</div> : null}
            </>
          ) : (
            <>
              <div className="ct-tree-node-line">{node?.source?.normalizedText || node?.source?.text || "-"}</div>
              <div className="ct-tree-node-grid">
                <div><span>Operator</span><strong>{node?.logic?.logicalOperator || "-"}</strong></div>
                <div><span>Family</span><strong>{node?.compiler?.familyId || "-"}</strong></div>
                <div><span>Evaluator</span><strong>{node?.patientSupport?.evaluator || "-"}</strong></div>
                <div><span>Linkage block</span><strong>{node?.patientSupport?.blockedByLinkageConflict ? "Yes" : "No"}</strong></div>
              </div>
              {node?.logic?.predicate ? (
                <div className="ct-tree-node-subtle">
                  Predicate: <strong>{node.logic.predicate}</strong> {node?.logic?.comparator || ""} {String(node?.logic?.value ?? "")}
                </div>
              ) : null}
              {Array.isArray(node?.patientSupport?.requiredPatientFeatures) && node.patientSupport.requiredPatientFeatures.length > 0 ? (
                <div className="ct-tree-node-subtle">
                  Required patient features: {node.patientSupport.requiredPatientFeatures.join(", ")}
                </div>
              ) : null}
              {Array.isArray(node?.patientSupport?.evidencePaths) && node.patientSupport.evidencePaths.length > 0 ? (
                <div className="ct-tree-node-subtle">
                  Evidence paths: {node.patientSupport.evidencePaths.join(", ")}
                </div>
              ) : null}
              {tab === "runtime" && node?.runtime ? (
                <>
                  <div className="ct-tree-node-subtle">
                    Reason: {node.runtime.reason || "-"}
                  </div>
                  {Array.isArray(node?.runtime?.evidenceRefs) && node.runtime.evidenceRefs.length > 0 ? (
                    <div className="ct-tree-node-subtle">
                      Evidence refs: {node.runtime.evidenceRefs.join(", ")}
                    </div>
                  ) : null}
                </>
              ) : null}
            </>
          )}
        </div>
      </div>
      {hasChildren ? (
        <div className="ct-tree-children">
          {node.children.map((child) => (
            <TreeNode key={child.nodeId} node={child} tab={tab} depth={depth + 1} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function ClinicalTrialCanonicalWorkspace({
  trial,
  treePayload,
  runtimeResult = null,
  runtimeLabel = "",
}) {
  const [activeTab, setActiveTab] = useState("source");
  const [filters, setFilters] = useState(DEFAULT_FILTERS);

  useEffect(() => {
    setFilters(DEFAULT_FILTERS);
  }, [treePayload?.trialId]);

  const runtimeMap = useMemo(() => buildRuntimeRowMap(runtimeResult), [runtimeResult]);
  const annotatedRoots = useMemo(
    () => (Array.isArray(treePayload?.roots) ? treePayload.roots.map((root) => annotateRuntime(root, runtimeMap)) : []),
    [treePayload?.roots, runtimeMap]
  );
  const runtimeAvailable = Boolean(runtimeResult);
  const filteredRoots = useMemo(
    () => annotatedRoots.map((root) => filterTree(root, filters, runtimeAvailable)).filter(Boolean),
    [annotatedRoots, filters, runtimeAvailable]
  );

  const canonicalSummary = treePayload?.canonicalSummary || {};
  const sourceSummary = treePayload?.sourceSummary || {};
  const runtimeReadiness = treePayload?.runtimeReadiness || trial?.runtimeReadiness || {};

  const toggleFilter = (filterId) => {
    setFilters((previous) => ({
      ...previous,
      [filterId]: !previous[filterId],
    }));
  };

  if (!treePayload) {
    return (
      <section className="pharma-card">
        <p className="pharma-muted">Loading canonical tree...</p>
      </section>
    );
  }

  return (
    <section className="pharma-card ct-workspace-card">
      <div className="pharma-step-label"><span className="pharma-step-num">3</span> Trial Eligibility Workspace</div>
      <div className="ct-workspace-summary">
        <div className="pharma-kv-grid">
          <div><span>Source bullets</span><strong>{sourceSummary?.sourceBullets ?? 0}</strong></div>
          <div><span>Group headers</span><strong>{sourceSummary?.groupHeaders ?? 0}</strong></div>
          <div><span>Structured criteria</span><strong>{sourceSummary?.structuredCriteria ?? 0}</strong></div>
          <div><span>Notes</span><strong>{sourceSummary?.notes ?? 0}</strong></div>
        </div>
        <div className="pharma-kv-grid">
          <div><span>Executable predicates</span><strong>{canonicalSummary?.executablePredicates ?? 0}</strong></div>
          <div><span>Deterministic</span><strong>{canonicalSummary?.deterministic ?? 0}</strong></div>
          <div><span>Manual</span><strong>{canonicalSummary?.manualReview ?? 0}</strong></div>
          <div><span>Unsupported</span><strong>{canonicalSummary?.unsupported ?? 0}</strong></div>
          <div><span>Not-patient rules</span><strong>{canonicalSummary?.notPatientRules ?? 0}</strong></div>
          <div><span>Prefilter eligible</span><strong>{canonicalSummary?.prefilterEligible ?? 0}</strong></div>
          <div><span>Runtime ready</span><strong>{canonicalSummary?.runtimeReady ? "Yes" : "No"}</strong></div>
          <div><span>Readiness reason</span><strong>{runtimeReadiness?.reasonIfBlocked || "Ready"}</strong></div>
        </div>
      </div>

      <div className="ct-workspace-tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`ct-workspace-tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="ct-workspace-filters">
        {FILTER_DEFS.map((filterDef) => (
          <button
            key={filterDef.id}
            type="button"
            className={`pharma-chip ${filters[filterDef.id] ? "active" : ""}`}
            onClick={() => toggleFilter(filterDef.id)}
          >
            {filterDef.label}
          </button>
        ))}
      </div>

      {activeTab === "runtime" ? (
        <div className="ct-runtime-banner">
          {runtimeResult ? (
            <div className="pharma-evidence-chip-row">
              <span className={`pharma-assessment-chip ${formatRuntimeChip(runtimeResult?.overall).className}`}>
                {formatRuntimeChip(runtimeResult?.overall).label}
              </span>
              {runtimeLabel ? <span className="pharma-evidence-chip neutral">{runtimeLabel}</span> : null}
              {runtimeResult?.evidenceCompletenessPct != null ? (
                <span className="pharma-evidence-chip neutral">
                  Evidence {Number(runtimeResult.evidenceCompletenessPct).toFixed(1)}%
                </span>
              ) : null}
            </div>
          ) : (
            <p className="pharma-muted">
              Select a patient or candidate result to annotate the canonical tree with runtime outcomes.
            </p>
          )}
        </div>
      ) : null}

      <div className="ct-tree-root-wrap">
        {filteredRoots.map((root) => (
          <TreeNode key={root.nodeId} node={root} tab={activeTab} depth={0} />
        ))}
      </div>
    </section>
  );
}
