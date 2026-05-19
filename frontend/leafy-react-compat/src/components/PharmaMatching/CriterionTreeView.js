import React, { useEffect, useMemo, useState } from "react";
import { StatusChip } from "../PharmaDesignSystem/PharmaDesignSystem";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TABS = [
  { id: "source", label: "Source" },
  { id: "canonical", label: "Canonical Logic" },
  { id: "runtime", label: "Runtime" },
];

export const FILTER_DEFS = [
  { id: "deterministicOnly", label: "Deterministic only" },
  { id: "patientImpactingOnly", label: "Patient-impacting only" },
  { id: "unresolvedOnly", label: "Unresolved only" },
  { id: "prefilterEligibleOnly", label: "Prefilter eligible only" },
  { id: "blockersOnly", label: "Blockers only" },
  { id: "reviewedOnly", label: "Reviewed only" },
];

export const DEFAULT_FILTERS = FILTER_DEFS.reduce(
  (acc, filterDef) => ({ ...acc, [filterDef.id]: false }),
  {}
);

export const STATUS_CLASS_MAP = {
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

export const STATUS_LABEL_MAP = {
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

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

const formatLabel = (value) =>
  STATUS_LABEL_MAP[String(value || "").trim().toLowerCase()] ||
  String(value || "-").replaceAll("_", " ");

const formatRuntimeChip = (value) => ({
  label: formatLabel(value),
  className: STATUS_CLASS_MAP[String(value || "").trim().toLowerCase()] || "neutral",
});

export const summarizeRuntime = (children = [], runtimeRow = null) => {
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

export const buildRuntimeRowMap = (runtimeResult) => {
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

export const annotateRuntime = (node, runtimeMap) => {
  const children = Array.isArray(node?.children)
    ? node.children.map((child) => annotateRuntime(child, runtimeMap))
    : [];
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
    filters.unresolvedOnly &&
    !(
      ["manual", "unsupported"].includes(node?.matchability?.class) ||
      ["partial_parse", "source_only"].includes(node?.compiler?.state) ||
      node?.review?.status === "pending"
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
      const hasRuntimeBlocker =
        BLOCKER_STATUSES.has(runtimeStatus) ||
        Number(runtimeSummary.not_met || 0) > 0 ||
        Number(runtimeSummary.unknown || 0) > 0 ||
        Number(runtimeSummary.manual_review_required || 0) > 0 ||
        Number(runtimeSummary.blocked_by_linkage_conflict || 0) > 0 ||
        Number(runtimeSummary.fillable_by_questionnaire || 0) > 0;
      if (!hasRuntimeBlocker) return false;
    } else if (!node?.matchability?.runtimeBlocker) {
      return false;
    }
  }

  return true;
};

export const filterTree = (node, filters, runtimeAvailable) => {
  const children = Array.isArray(node?.children)
    ? node.children
        .map((child) => filterTree(child, filters, runtimeAvailable))
        .filter(Boolean)
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

// ---------------------------------------------------------------------------
// RuntimeSummaryChips
// ---------------------------------------------------------------------------

export function RuntimeSummaryChips({ summary }) {
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
          <span
            key={key}
            className={`pharma-evidence-chip ${formatRuntimeChip(key).className}`}
          >
            {label}: {Number(summary?.[key] || 0)}
          </span>
        ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TreeNode
// ---------------------------------------------------------------------------

/**
 * Compact human-readable family label.
 * "demographics.age.min" → "Age (min)"
 */
const humanizeFamilyId = (familyId) => {
  const FAMILY_DISPLAY = {
    "demographics.age.min": "Age (min)",
    "demographics.age.max": "Age (max)",
    "demographics.sex.allowed": "Sex",
    "eligibility.healthy_volunteers.allowed": "Healthy Volunteers",
    "oncology.primary_cancer.required": "Primary Cancer",
    "oncology.advanced_or_metastatic.required": "Advanced / Metastatic",
    "biomarkers.ER.status.required": "ER Status",
    "biomarkers.HER2.status.required": "HER2 Status",
    "performance.ecog.max": "ECOG Performance",
    "organ_function.lab_panel": "Lab Panel",
    "therapy.prior_exposure.prohibited_history": "Prior Therapy",
    "context.pregnancy_breastfeeding": "Pregnancy / Breastfeeding",
    "context.infection_recent_systemic_therapy": "Infection / Recent Therapy",
    "concomitant_illness.significant": "Concomitant Illness",
    "sample_submission.willingness": "Sample Submission",
    "metastases.cns.active": "CNS Metastases",
  };
  if (FAMILY_DISPLAY[familyId]) return FAMILY_DISPLAY[familyId];
  if (!familyId) return "";
  const parts = String(familyId).split(".");
  return parts[parts.length - 1].replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
};

/**
 * Determine the matchability color class for a node.
 */
const matchabilityClass = (node) => {
  if (node?.matchability?.class === "deterministic") return "pass";
  if (node?.matchability?.runtimeBlocker) return "blocked";
  if (node?.matchability?.class === "manual") return "review";
  return "neutral";
};

export function TreeNode({ node, tab, depth = 0, selectedNodeId = "", onNodeSelect = null }) {
  const [expanded, setExpanded] = React.useState(false);
  const runtimeStatus = String(node?.runtime?.status || "").trim().toLowerCase();
  const runtimeChip = runtimeStatus ? formatRuntimeChip(runtimeStatus) : null;
  const hasChildren = Array.isArray(node?.children) && node.children.length > 0;
  const isSectionRoot = String(node?.nodeType || "") === "section_root";
  const familyId = node?.compiler?.familyId || "";
  const familyLabel = humanizeFamilyId(familyId);
  const isDeterministic = node?.matchability?.class === "deterministic";
  const hasPredicate = Boolean(node?.logic?.predicate);
  const displayText = node?.source?.normalizedText || node?.source?.text || node?.label || "-";
  const truncatedText = displayText.length > 140 ? `${displayText.slice(0, 140)}...` : displayText;
  const isSelected = Boolean(selectedNodeId) && String(selectedNodeId) === String(node?.nodeId || "");

  // Section root nodes render as simple headers
  if (isSectionRoot) {
    return (
      <div className={`ct-tree-node ${tab}`} style={{ "--ct-depth": depth }}>
        <div style={{
          fontWeight: 700, fontSize: 14, textTransform: "capitalize",
          padding: "10px 0 6px", color: "var(--pharma-text, #0f172a)",
          borderBottom: "2px solid var(--pharma-border, #e2e8f0)",
          marginBottom: 8,
        }}>
          {node?.label || formatLabel(node?.section) || "Section"}
          {hasChildren ? (
            <span style={{ fontWeight: 400, fontSize: 12, color: "var(--pharma-text-muted, #94a3b8)", marginLeft: 8 }}>
              {node.children.length} criteria
            </span>
          ) : null}
        </div>
        {hasChildren ? (
          <div className="ct-tree-children">
            {node.children.map((child) => (
              <TreeNode
                key={child.nodeId}
                node={child}
                tab={tab}
                depth={depth + 1}
                selectedNodeId={selectedNodeId}
                onNodeSelect={onNodeSelect}
              />
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className={`ct-tree-node ${tab}`} style={{ "--ct-depth": depth }}>
      <div
        className="ct-tree-node-card"
        style={{
          borderLeftColor: isDeterministic
            ? "var(--pharma-runtime, #00684A)"
            : node?.matchability?.runtimeBlocker
              ? "var(--pharma-fail, #991b1b)"
              : node?.matchability?.class === "manual"
                ? "var(--pharma-manual, #ea580c)"
                : "var(--pharma-border, #e2e8f0)",
          boxShadow: isSelected ? "0 0 0 2px rgba(59,130,246,0.18)" : "none",
          background: isSelected ? "var(--pharma-canonical-bg, #eff6ff)" : undefined,
          cursor: "pointer",
          transition: "box-shadow 0.15s",
        }}
        onClick={() => {
          if (typeof onNodeSelect === "function") {
            onNodeSelect(node);
          }
          setExpanded((prev) => !prev);
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            if (typeof onNodeSelect === "function") {
              onNodeSelect(node);
            }
            setExpanded((prev) => !prev);
          }
        }}
      >
        {/* ── Compact header: always visible ── */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--pharma-text, #0f172a)", lineHeight: 1.4 }}>
              {tab === "source" ? displayText : truncatedText}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
              {tab !== "source" && familyLabel ? (
                <span className={`pharma-evidence-chip ${matchabilityClass(node)}`} style={{ fontSize: 10 }}>
                  {familyLabel}
                </span>
              ) : null}
              {tab !== "source" ? (
                <span className={`pharma-evidence-chip ${matchabilityClass(node)}`} style={{ fontSize: 10 }}>
                  {formatLabel(node?.matchability?.class)}
                </span>
              ) : null}
              {tab !== "source" && node?.matchability?.prefilterEligible ? (
                <span className="pharma-evidence-chip pass" style={{ fontSize: 10 }}>Prefilter</span>
              ) : null}
              {tab === "runtime" && runtimeChip ? (
                <StatusChip status={runtimeStatus} label={runtimeChip.label} size="sm" />
              ) : null}
            </div>
            {tab !== "source" && hasPredicate && !expanded ? (
              <div style={{ fontSize: 12, color: "var(--pharma-brand, #00684A)", marginTop: 4, fontFamily: "monospace" }}>
                {node.logic.predicate} {node.logic?.comparator || ""} {String(node.logic?.value ?? "")}
              </div>
            ) : null}
          </div>
          {/* Expand indicator */}
          <span style={{
            fontSize: 11, color: "var(--pharma-text-muted, #94a3b8)",
            flexShrink: 0, marginTop: 2, transition: "transform 0.15s",
            transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
          }}>
            &#9660;
          </span>
        </div>

        {/* ── Runtime summary chips (for parent nodes with children) ── */}
        {tab === "runtime" && hasChildren && !expanded ? (
          <div style={{ marginTop: 8 }}>
            <RuntimeSummaryChips summary={node?.runtimeSummary} />
          </div>
        ) : null}

        {/* ── Expanded detail ── */}
        {expanded ? (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--pharma-border, #e2e8f0)" }}>
            {tab === "source" ? (
              <>
                {Array.isArray(node?.source?.ancestorHeaders) && node.source.ancestorHeaders.length > 0 ? (
                  <div style={{ fontSize: 12, color: "var(--pharma-text-secondary, #475569)", marginBottom: 6 }}>
                    Context: {node.source.ancestorHeaders.join(" > ")}
                  </div>
                ) : null}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  <span className="pharma-evidence-chip neutral" style={{ fontSize: 10 }}>{node?.sourceRef || "-"}</span>
                  <span className="pharma-evidence-chip neutral" style={{ fontSize: 10 }}>{formatLabel(node?.section)}</span>
                  <span className="pharma-evidence-chip neutral" style={{ fontSize: 10 }}>{formatLabel(node?.nodeType)}</span>
                </div>
              </>
            ) : (
              <>
                {/* Full criterion text */}
                {(node?.source?.normalizedText || node?.source?.text) && truncatedText !== displayText ? (
                  <div style={{ fontSize: 13, color: "var(--pharma-text, #0f172a)", lineHeight: 1.5, marginBottom: 8 }}>
                    {displayText}
                  </div>
                ) : null}

                {/* Compact metadata grid */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8, marginBottom: 8 }}>
                  {familyId ? (
                    <div>
                      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--pharma-text-muted, #94a3b8)" }}>Family</div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--pharma-text, #0f172a)", marginTop: 2 }}>{familyId}</div>
                    </div>
                  ) : null}
                  {node?.patientSupport?.evaluator ? (
                    <div>
                      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--pharma-text-muted, #94a3b8)" }}>Evaluator</div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--pharma-text, #0f172a)", marginTop: 2 }}>{node.patientSupport.evaluator}</div>
                    </div>
                  ) : null}
                  {node?.compiler?.state ? (
                    <div>
                      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--pharma-text-muted, #94a3b8)" }}>Compiler</div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--pharma-text, #0f172a)", marginTop: 2 }}>{formatLabel(node.compiler.state)}</div>
                    </div>
                  ) : null}
                </div>

                {/* Predicate */}
                {hasPredicate ? (
                  <div style={{
                    padding: "6px 10px", borderRadius: 6,
                    background: "var(--pharma-runtime-bg, #dcfce7)",
                    fontSize: 12, fontFamily: "monospace", color: "var(--pharma-runtime, #00684A)",
                    marginBottom: 8,
                  }}>
                    {node.logic.predicate} {node.logic?.comparator || ""} {String(node.logic?.value ?? "")}
                  </div>
                ) : null}

                {/* Extra metadata chips */}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  <span className="pharma-evidence-chip neutral" style={{ fontSize: 10 }}>{node?.sourceRef || "-"}</span>
                  {node?.review?.status === "reviewed" ? (
                    <span className="pharma-evidence-chip pass" style={{ fontSize: 10 }}>Reviewed</span>
                  ) : null}
                  {node?.matchability?.questionnaireFillable ? (
                    <span className="pharma-evidence-chip review" style={{ fontSize: 10 }}>Questionnaire</span>
                  ) : null}
                  {node?.patientSupport?.blockedByLinkageConflict ? (
                    <span className="pharma-evidence-chip blocked" style={{ fontSize: 10 }}>Linkage block</span>
                  ) : null}
                </div>

                {/* Runtime detail */}
                {tab === "runtime" && node?.runtime ? (
                  <div style={{ marginTop: 8, padding: "6px 10px", borderRadius: 6, background: "var(--pharma-bg, #f8fafc)", fontSize: 12 }}>
                    <div style={{ color: "var(--pharma-text-secondary, #475569)" }}>{node.runtime.reason || "-"}</div>
                    {Array.isArray(node.runtime?.evidenceRefs) && node.runtime.evidenceRefs.length > 0 ? (
                      <div style={{ marginTop: 4, color: "var(--pharma-text-muted, #94a3b8)", fontSize: 11 }}>
                        Evidence: {node.runtime.evidenceRefs.join(", ")}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </>
            )}
          </div>
        ) : null}
      </div>
      {hasChildren ? (
        <div className="ct-tree-children">
          {node.children.map((child) => (
            <TreeNode
              key={child.nodeId}
              node={child}
              tab={tab}
              depth={depth + 1}
              selectedNodeId={selectedNodeId}
              onNodeSelect={onNodeSelect}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CriterionTreeView (default export)
// ---------------------------------------------------------------------------

export default function CriterionTreeView({
  treePayload,
  runtimeResult = null,
  runtimeLabel = "",
  activeTab: externalTab,
  onTabChange,
  filters: externalFilters,
  onFilterChange,
  showTabs = true,
  showFilters = true,
  showSummary = true,
  compact = false,
  selectedNodeId = "",
  onNodeSelect = null,
}) {
  // ---- Tab state (controlled or internal) --------------------------------
  const [internalTab, setInternalTab] = useState("canonical");
  const activeTab = externalTab !== undefined ? externalTab : internalTab;
  const setActiveTab = onTabChange || setInternalTab;

  // ---- Filter state (controlled or internal) -----------------------------
  const [internalFilters, setInternalFilters] = useState(DEFAULT_FILTERS);
  const filters = externalFilters !== undefined ? externalFilters : internalFilters;
  const setFilters = onFilterChange || setInternalFilters;

  // Reset internal filters when the tree changes
  useEffect(() => {
    if (!externalFilters) {
      setInternalFilters(DEFAULT_FILTERS);
    }
  }, [treePayload?.trialId, externalFilters]);

  // ---- Derived data ------------------------------------------------------
  const runtimeMap = useMemo(
    () => buildRuntimeRowMap(runtimeResult),
    [runtimeResult]
  );

  const annotatedRoots = useMemo(
    () =>
      Array.isArray(treePayload?.roots)
        ? treePayload.roots.map((root) => annotateRuntime(root, runtimeMap))
        : [],
    [treePayload?.roots, runtimeMap]
  );

  const runtimeAvailable = Boolean(runtimeResult);

  const filteredRoots = useMemo(
    () =>
      annotatedRoots
        .map((root) => filterTree(root, filters, runtimeAvailable))
        .filter(Boolean),
    [annotatedRoots, filters, runtimeAvailable]
  );

  const canonicalSummary = treePayload?.canonicalSummary || {};
  const sourceSummary = treePayload?.sourceSummary || {};
  const runtimeReadiness = treePayload?.runtimeReadiness || {};

  // ---- Filter toggle helper ---------------------------------------------
  const toggleFilter = (filterId) => {
    const next = { ...filters, [filterId]: !filters[filterId] };
    setFilters(next);
  };

  // ---- Loading state -----------------------------------------------------
  if (!treePayload) {
    return (
      <section className={`pharma-card${compact ? " compact" : ""}`}>
        <p className="pharma-muted">Loading canonical tree...</p>
      </section>
    );
  }

  return (
    <section className={`pharma-card ct-workspace-card${compact ? " compact" : ""}`}>
      {/* Summary metrics */}
      {showSummary ? (
        <div className="ct-workspace-summary">
          <div className="pharma-kv-grid">
            <div>
              <span>Source bullets</span>
              <strong>{sourceSummary?.sourceBullets ?? 0}</strong>
            </div>
            <div>
              <span>Group headers</span>
              <strong>{sourceSummary?.groupHeaders ?? 0}</strong>
            </div>
            <div>
              <span>Structured criteria</span>
              <strong>{sourceSummary?.structuredCriteria ?? 0}</strong>
            </div>
            <div>
              <span>Notes</span>
              <strong>{sourceSummary?.notes ?? 0}</strong>
            </div>
          </div>
          <div className="pharma-kv-grid">
            <div>
              <span>Executable predicates</span>
              <strong>{canonicalSummary?.executablePredicates ?? 0}</strong>
            </div>
            <div>
              <span>Deterministic</span>
              <strong>{canonicalSummary?.deterministic ?? 0}</strong>
            </div>
            <div>
              <span>Manual</span>
              <strong>{canonicalSummary?.manualReview ?? 0}</strong>
            </div>
            <div>
              <span>Unsupported</span>
              <strong>{canonicalSummary?.unsupported ?? 0}</strong>
            </div>
            <div>
              <span>Not-patient rules</span>
              <strong>{canonicalSummary?.notPatientRules ?? 0}</strong>
            </div>
            <div>
              <span>Prefilter eligible</span>
              <strong>{canonicalSummary?.prefilterEligible ?? 0}</strong>
            </div>
            <div>
              <span>Runtime ready</span>
              <strong>{canonicalSummary?.runtimeReady ? "Yes" : "No"}</strong>
            </div>
            <div>
              <span>Readiness reason</span>
              <strong>{runtimeReadiness?.reasonIfBlocked || "Ready"}</strong>
            </div>
          </div>
        </div>
      ) : null}

      {/* Tab bar */}
      {showTabs ? (
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
      ) : null}

      {/* Filter chip bar */}
      {showFilters ? (
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
      ) : null}

      {/* Runtime banner */}
      {activeTab === "runtime" ? (
        <div className="ct-runtime-banner">
          {runtimeResult ? (
            <div className="pharma-evidence-chip-row">
              <span
                className={`pharma-assessment-chip ${
                  formatRuntimeChip(runtimeResult?.overall).className
                }`}
              >
                {formatRuntimeChip(runtimeResult?.overall).label}
              </span>
              {runtimeLabel ? (
                <span className="pharma-evidence-chip neutral">
                  {runtimeLabel}
                </span>
              ) : null}
              {runtimeResult?.evidenceCompletenessPct != null ? (
                <span className="pharma-evidence-chip neutral">
                  Evidence{" "}
                  {Number(runtimeResult.evidenceCompletenessPct).toFixed(1)}%
                </span>
              ) : null}
            </div>
          ) : (
            <p className="pharma-muted">
              Select a patient or candidate result to annotate the canonical
              tree with runtime outcomes.
            </p>
          )}
        </div>
      ) : null}

      {/* Tree nodes */}
      <div className="ct-tree-root-wrap">
        {filteredRoots.map((root) => (
          <TreeNode
            key={root.nodeId}
            node={root}
            tab={activeTab}
            depth={0}
            selectedNodeId={selectedNodeId}
            onNodeSelect={onNodeSelect}
          />
        ))}
      </div>
    </section>
  );
}
