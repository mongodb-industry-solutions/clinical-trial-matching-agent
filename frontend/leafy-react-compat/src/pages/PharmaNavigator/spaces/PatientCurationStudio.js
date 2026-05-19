import React, { useState, useCallback, useEffect, useMemo } from "react";
import {
  SpaceHeader, StatusChip, ReadinessScorecard, CriterionStatusBar,
  SectionDivider, TransformationArrow, CriterionCard,
} from "../../../components/PharmaDesignSystem/PharmaDesignSystem";
import { CoverageMeter } from "../../../components/PharmaMatching";
import ReportCard from "../../../components/ReportCard/ReportCard";
import {
  APIGetRandomPharmaPatientContext,
  APIGetProductPatientWorkspace,
  APIListProductPatients,
  APIGetSyntheaPatientEmr,
} from "../../../services/APIService";

/* --------------------------------------------------------------------------
   Constants & helpers
   -------------------------------------------------------------------------- */

const TABS = [
  { key: "health", label: "Source Record" },
  { key: "timeline", label: "Timeline" },
  { key: "evidence", label: "Curated Evidence" },
  { key: "profile", label: "Match Readiness" },
  { key: "gaps", label: "Gaps & Identity" },
];

const EVIDENCE_FAMILIES = [
  { key: "demographics", label: "Demographics", features: ["age", "sex"] },
  { key: "primaryCancer", label: "Primary Cancer", features: ["primaryCancer"] },
  { key: "biomarkers", label: "Biomarkers", features: ["ER", "PR", "HER2"] },
  { key: "stageExtent", label: "Stage & Extent", features: ["stageGroup", "metastaticFlag", "diseaseExtent", "metastaticSites"] },
  { key: "treatmentHistory", label: "Treatment History", features: ["treatmentHistory"] },
  { key: "labs", label: "Key Labs", features: ["keyLabs"] },
  { key: "performanceStatus", label: "Performance Status", features: ["ECOG"] },
  { key: "blockers", label: "Blockers", features: ["cnsMetastasesActive", "activeInfection"] },
  { key: "screeningContext", label: "Screening Context", features: ["pregnancy", "breastfeeding"] },
];

const EVENT_TYPE_LABELS = {
  condition: "Condition",
  medication: "Medication",
  procedure: "Procedure",
  observation: "Observation",
  lab: "Lab",
  encounter: "Encounter",
  immunization: "Immunization",
  allergy: "Allergy",
  other: "Other",
};

const EVENT_TYPE_TONES = {
  condition: "not-met",
  medication: "fillable",
  procedure: "manual",
  observation: "canonical",
  lab: "met",
  encounter: "neutral",
  immunization: "source",
  allergy: "conflict",
  other: "unknown",
};

const formatDateShort = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "Undated";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
};

const formatMonthKey = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "Undated";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "Undated";
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "long" });
};

const classifyEventType = (resourceType) => {
  const normalized = String(resourceType || "").toLowerCase();
  if (normalized.includes("condition")) return "condition";
  if (normalized.includes("medication")) return "medication";
  if (normalized.includes("procedure")) return "procedure";
  if (normalized.includes("observation")) return "observation";
  if (normalized.includes("lab") || normalized.includes("diagnostic")) return "lab";
  if (normalized.includes("encounter")) return "encounter";
  if (normalized.includes("immunization")) return "immunization";
  if (normalized.includes("allergy")) return "allergy";
  return "other";
};

const humanizeFeatureKey = (key) =>
  String(key || "")
    .replace(/([A-Z])/g, " $1")
    .replace(/_/g, " ")
    .replace(/^\s/, "")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();

const normalizePatientRef = (value) => {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  return normalized.startsWith("Patient/") ? normalized : `Patient/${normalized}`;
};

const resolveLinkedPatientRef = (contextPayload, evidencePayload, matchPayload) => {
  const context = contextPayload || {};
  const evidenceResponse = evidencePayload || {};
  const matchResponse = matchPayload || {};
  const evidence = evidenceResponse?.patientEvidenceProfile || evidenceResponse || {};
  const matchProfile = matchResponse?.patientMatchProfile || matchResponse || {};
  const evidenceSourceRefs = Array.isArray(evidence?.identity?.sourceRefs) ? evidence.identity.sourceRefs : [];
  const matchSourceRefs = Array.isArray(matchProfile?.identity?.sourceRefs) ? matchProfile.identity.sourceRefs : [];
  const sourceRef =
    evidenceSourceRefs.find((item) => String(item || "").trim().startsWith("Patient/"))
    || matchSourceRefs.find((item) => String(item || "").trim().startsWith("Patient/"))
    || "";

  return normalizePatientRef(
    context?.patientLink?.syntheaPatientRef
    || context?.patientRef
    || context?.ref
    || context?.patientContext?.syntheaPatientRef
    || context?.patientContext?.patientRef
    || evidenceResponse?.patientRef
    || evidence?.patientRef
    || evidence?.identity?.syntheaPatientRef
    || matchResponse?.patientRef
    || matchProfile?.patientRef
    || matchProfile?.identity?.syntheaPatientRef
    || sourceRef
    || ""
  );
};

const resolvedValueDisplay = (resolved) => {
  if (resolved == null) return "Unknown";
  if (typeof resolved === "boolean") return resolved ? "Yes" : "No";
  if (Array.isArray(resolved)) return resolved.length > 0 ? resolved.join(", ") : "None";
  if (typeof resolved === "object") {
    if (resolved.text) return resolved.text;
    if (resolved.code) return resolved.code;
    // Handle nested lab-style objects: { hemoglobin: { value: 10.8, unit: "g/dL" }, ... }
    const keys = Object.keys(resolved);
    if (keys.length === 0) return "No data";
    const formatted = keys.map((k) => {
      const v = resolved[k];
      if (v == null) return `${k}: —`;
      if (typeof v === "object" && !Array.isArray(v)) {
        // Lab-style: { value: 10.8, unit: "g/dL" }
        if (v.value != null && v.unit) return `${k}: ${v.value} ${v.unit}`;
        if (v.value != null) return `${k}: ${v.value}`;
        if (v.text) return `${k}: ${v.text}`;
        if (v.code) return `${k}: ${v.code}`;
        if (v.status) return `${k}: ${v.status}`;
        return `${k}: present`;
      }
      return `${k}: ${String(v)}`;
    });
    return formatted.join(" · ");
  }
  return String(resolved);
};

const featureStatusForChip = (status) => {
  if (!status) return "unknown";
  const s = String(status).toLowerCase();
  if (s === "present" || s === "positive") return "met";
  if (s === "absent" || s === "negative") return "not_met";
  if (s === "conflicting" || s === "conflict") return "conflict";
  if (s === "equivocal") return "manual";
  return "unknown";
};

const getPatientInitials = (ctx) => {
  const name = ctx?.patientContext?.patientName
    || ctx?.report?.user
    || "";
  if (!name) return null;
  const parts = String(name).trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return parts[0] ? parts[0][0].toUpperCase() : null;
};

/* --------------------------------------------------------------------------
   PatientCurationStudio
   -------------------------------------------------------------------------- */

export default function PatientCurationStudio({
  navigateSpace,
  setAppointmentId: propSetAppointmentId,
  setPatientId: propSetPatientId,
  selectedPatientRef,
  setSelectedPatientRef,
  benchmarkPackId,
  showcaseCase,
}) {
  const [activeTab, setActiveTab] = useState("health");
  const [patientContext, setPatientContext] = useState(null);
  const [evidenceProfile, setEvidenceProfile] = useState(null);
  const [matchProfile, setMatchProfile] = useState(null);
  const [emrPayload, setEmrPayload] = useState(null);
  const [loading, setLoading] = useState(false);
  const [emrLoading, setEmrLoading] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [patientCatalog, setPatientCatalog] = useState([]);
  const [catalogSummary, setCatalogSummary] = useState(null);
  const [patientSearch, setPatientSearch] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [error, setError] = useState("");
  const [loadedContextKey, setLoadedContextKey] = useState("");
  const defaultLoadedTab = showcaseCase ? "evidence" : "health";

  /* --- Derived data -------------------------------------------------------- */

  const resolved = useMemo(
    () => (evidenceProfile?.resolvedFeatures || {}),
    [evidenceProfile],
  );

  const identityBlock = useMemo(
    () => (evidenceProfile?.identity || {}),
    [evidenceProfile],
  );

  const linkageStatus = identityBlock.linkageStatus
    || matchProfile?.identity?.linkageStatus
    || patientContext?.patientSummary?.linkageStatus
    || "unlinked";

  const demographicsBlock = useMemo(
    () => (evidenceProfile?.demographics || {}),
    [evidenceProfile],
  );

  // oncologyBlock removed — was unused

  const runtimeReadiness = useMemo(
    () => (matchProfile?.runtimeReadiness || {}),
    [matchProfile],
  );

  const cohortMembership = useMemo(
    () => (matchProfile?.cohortMembership || {}),
    [matchProfile],
  );

  const matchFilters = useMemo(
    () => (matchProfile?.matchFilters || {}),
    [matchProfile],
  );

  const gaps = useMemo(
    () => (evidenceProfile?.gaps || matchProfile?.gaps || []),
    [evidenceProfile, matchProfile],
  );

  const completeness = useMemo(
    () => (evidenceProfile?.completeness || {}),
    [evidenceProfile],
  );

  const hasScreeningContext = useMemo(
    () =>
      Boolean(
        patientContext?.report
          || patientContext?.series?.length
          || patientContext?.clinicalNotes
          || patientContext?.conclusions
          || patientContext?.biopsyResults
          || patientContext?.screeningSummary?.present
          || patientContext?.patientSummary?.mergeSummary?.hasLeafyScreeningData
      ),
    [patientContext],
  );

  /* --- Timeline derived from EMR ------------------------------------------ */

  const timelineEvents = useMemo(() => {
    const timeline = emrPayload?.timeline || [];
    const resources = emrPayload?.resources || [];
    const source = timeline.length > 0 ? timeline : resources;
    return source.map((event) => ({
      ...event,
      _type: classifyEventType(event?.resourceType),
      _date: event?.date || event?.effectiveDateTime || event?.onsetDateTime || event?.period?.start || null,
    })).sort((a, b) => {
      if (!a._date && !b._date) return 0;
      if (!a._date) return 1;
      if (!b._date) return -1;
      return new Date(b._date) - new Date(a._date);
    });
  }, [emrPayload]);

  const [timelineFilter, setTimelineFilter] = useState(null);

  const timelineTypeCounts = useMemo(() => {
    const counts = {};
    timelineEvents.forEach((e) => { counts[e._type] = (counts[e._type] || 0) + 1; });
    return counts;
  }, [timelineEvents]);

  const filteredTimeline = useMemo(() => {
    if (!timelineFilter) return timelineEvents;
    return timelineEvents.filter((e) => e._type === timelineFilter);
  }, [timelineEvents, timelineFilter]);

  const monthGroups = useMemo(() => {
    const groups = new Map();
    filteredTimeline.forEach((event) => {
      const key = formatMonthKey(event._date);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    });
    return Array.from(groups.entries());
  }, [filteredTimeline]);

  /* --- Patient summary strip data ----------------------------------------- */

  const patientSummary = useMemo(() => {
    if (!patientContext && !evidenceProfile) return null;
    const initials = getPatientInitials(patientContext);
    const name = patientContext?.patientContext?.patientName
      || patientContext?.report?.user
      || identityBlock.canonicalPatientKey
      || "Patient";
    const age = demographicsBlock.ageYears;
    const sex = demographicsBlock.sex;
    const er = (resolved.ER || {}).resolvedValue || (resolved.ER || {}).status;
    const her2 = (resolved.HER2 || {}).resolvedValue || (resolved.HER2 || {}).status;
    const ecog = (resolved.ECOG || {}).resolvedValue;
    const metastatic = (resolved.metastaticFlag || {}).status;
    const primaryCancer = (resolved.primaryCancer || {}).resolvedValue;
    const cancerType = typeof primaryCancer === "object" ? (primaryCancer?.text || primaryCancer?.code) : primaryCancer;
    return { initials, name, age, sex, er, her2, ecog, metastatic, cancerType };
  }, [patientContext, evidenceProfile, resolved, demographicsBlock, identityBlock]);

  /* --- Readiness checklist ------------------------------------------------ */

  const readinessChecklist = useMemo(() => {
    if (!evidenceProfile && !matchProfile) return [];
    return [
      {
        label: "Linked identity",
        passed: linkageStatus === "linked",
      },
      {
        label: "Primary cancer confirmed",
        passed: (resolved.primaryCancer || {}).status === "present",
      },
      {
        label: "Biomarkers complete",
        passed: ["ER", "PR", "HER2"].every(
          (k) => (resolved[k] || {}).status !== "unknown",
        ),
      },
      {
        label: "Performance status",
        passed: (resolved.ECOG || {}).status === "present",
      },
      {
        label: "No critical conflicts",
        passed: (identityBlock.linkageConflicts || []).length === 0,
      },
    ];
  }, [evidenceProfile, matchProfile, linkageStatus, resolved, identityBlock]);

  /* --- API loaders --------------------------------------------------------- */

  const loadPatientCatalog = useCallback(async (searchValue = "") => {
    setCatalogLoading(true);
    setCatalogError("");
    try {
      const response = await APIListProductPatients({
        limit: 10,
        focus: "breast_cancer",
        search: searchValue || undefined,
      });
      setPatientCatalog(Array.isArray(response?.patients) ? response.patients : []);
      setCatalogSummary(response?.summary || null);
    } catch (err) {
      setPatientCatalog([]);
      setCatalogSummary(null);
      setCatalogError(err?.response?.data?.detail || err?.message || "Failed to load patient registry.");
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  const loadPatientWorkspace = useCallback(async (patientRefValue, workspaceSeed = null) => {
    const normalizedPatientRef = normalizePatientRef(patientRefValue || workspaceSeed?.patientRef);
    if (!normalizedPatientRef) return;

    setLoading(true);
    setError("");
    setPatientContext(null);
    setEvidenceProfile(null);
    setMatchProfile(null);
    setEmrPayload(null);
    setActiveTab(defaultLoadedTab);
    setEmrLoading(true);
    if (propSetAppointmentId) propSetAppointmentId("");
    if (propSetPatientId) propSetPatientId("");

    try {
      const workspace = workspaceSeed || await APIGetProductPatientWorkspace(normalizedPatientRef);
      const resolvedPatientRef = resolveLinkedPatientRef(
        workspace,
        workspace?.patientEvidenceProfile,
        workspace?.patientMatchProfile,
      ) || normalizedPatientRef;
      const patientSummaryPayload = workspace?.patientSummary || {};
      const screeningSummary = workspace?.screeningSummary || patientSummaryPayload?.screeningSummary || {};
      const evidencePayload = workspace?.patientEvidenceProfile || null;
      const matchPayload = workspace?.patientMatchProfile || null;

      setPatientContext({
        patientRef: resolvedPatientRef,
        patientSummary: patientSummaryPayload,
        patientLink: workspace?.patientLink || {
          syntheaPatientRef: resolvedPatientRef,
          linkageStatus:
            patientSummaryPayload?.linkageStatus
            || evidencePayload?.identity?.linkageStatus
            || matchPayload?.identity?.linkageStatus
            || "unknown",
        },
        screeningSummary,
        patientContext: {
          patientName:
            patientSummaryPayload?.displayName
            || evidencePayload?.demographics?.name
            || evidencePayload?.identity?.canonicalPatientKey
            || matchPayload?.identity?.canonicalPatientKey
            || resolvedPatientRef,
          patientID: screeningSummary?.leafyPatientId || "",
        },
      });
      setEvidenceProfile(evidencePayload);
      setMatchProfile(matchPayload);
      setLoadedContextKey(`patientRef::${resolvedPatientRef}`);
      if (setSelectedPatientRef) setSelectedPatientRef(resolvedPatientRef);

      const emrRes = await APIGetSyntheaPatientEmr(resolvedPatientRef).catch(() => null);
      setEmrPayload(emrRes);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Failed to load patient workspace.");
    } finally {
      setEmrLoading(false);
      setLoading(false);
    }
  }, [defaultLoadedTab, propSetAppointmentId, propSetPatientId, setSelectedPatientRef]);

  const handleLoadRandom = useCallback(async () => {
    try {
      const workspace = await APIGetRandomPharmaPatientContext();
      const resolvedPatientRef = resolveLinkedPatientRef(
        workspace,
        workspace?.patientEvidenceProfile,
        workspace?.patientMatchProfile,
      );
      if (!resolvedPatientRef) {
        throw new Error("Random patient selection did not include a patient reference.");
      }
      await loadPatientWorkspace(resolvedPatientRef, workspace);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Failed to load random patient.");
    }
  }, [loadPatientWorkspace]);

  /* --- Auto-load on mount -------------------------------------------------- */

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      loadPatientCatalog(patientSearch);
    }, 180);
    return () => clearTimeout(timeoutId);
  }, [loadPatientCatalog, patientSearch]);

  useEffect(() => {
    if (!selectedPatientRef) return;
    const nextContextKey = `patientRef::${selectedPatientRef}`;
    if (nextContextKey !== loadedContextKey) {
      loadPatientWorkspace(selectedPatientRef);
    }
  }, [selectedPatientRef, loadedContextKey, loadPatientWorkspace]);

  /* --- Render -------------------------------------------------------------- */

  const isPatientLoaded = Boolean(patientContext || evidenceProfile || matchProfile);

  return (
    <div className="pds-space pds-space--patient-curation">
      <SpaceHeader
        title="Patient Graph"
        subtitle="Resolve one patient into a unified evidence graph and a match-ready runtime profile."
        onBack={() => navigateSpace("landing")}
        backLabel="Workbench"
      />

      {(showcaseCase || benchmarkPackId) && (
        <div className="pds-card" style={{ padding: "12px 16px" }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {showcaseCase && (
              <StatusChip status="canonical" label={`Showcase: ${String(showcaseCase).replace(/_/g, " ")}`} size="sm" />
            )}
            {benchmarkPackId && (
              <StatusChip status="source" label={`Pack: ${benchmarkPackId}`} size="sm" />
            )}
            {selectedPatientRef && (
              <StatusChip status="neutral" label={`PatientRef: ${selectedPatientRef}`} size="sm" />
            )}
          </div>
        </div>
      )}

      {/* ---- Patient Selector -------------------------------------------- */}
      <div className="pds-card" style={{ padding: 18 }}>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 4 }}>
          Step 1
        </div>
        <h3 style={{ fontSize: 18, fontWeight: 800, margin: 0, color: "var(--pharma-text, #0f172a)" }}>Load one patient</h3>
        <div className="pds-muted" style={{ fontSize: 13, marginTop: 6, lineHeight: 1.5 }}>
          Search the product patient registry, then inspect how raw record evidence becomes a match-ready profile.
        </div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", margin: "14px 0 12px" }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--pharma-text, #0f172a)" }}>Patient source</div>
          {isPatientLoaded && !loading && (
            <StatusChip status="met" label="Loaded" size="sm" />
          )}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 280px) minmax(0, 1fr)", gap: "14px", alignItems: "start" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="pds-field">
              <label className="pds-field__label">Search the patient registry</label>
              <input
                className="pds-input"
                type="text"
                placeholder="Patient ref, ER, HER2, stage"
                value={patientSearch}
                onChange={(e) => setPatientSearch(e.target.value)}
              />
            </div>
            <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
              The picker now uses the product workspace catalog only. Each row opens the same evidence, readiness, and gap review flow.
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                className="pds-btn pds-btn--secondary"
                onClick={handleLoadRandom}
                disabled={loading}
              >
                {loading ? "Loading..." : "Load Random Patient"}
              </button>
              {selectedPatientRef && (
                <button
                  className="pds-btn pds-btn--ghost"
                  onClick={() => loadPatientWorkspace(selectedPatientRef)}
                  disabled={loading}
                >
                  Reload Current
                </button>
              )}
            </div>
            {catalogSummary && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <StatusChip status="source" label={`${catalogSummary.totalCount || 0} patients`} size="sm" />
                <StatusChip status="met" label={`${catalogSummary.runnableCount || 0} runnable`} size="sm" />
                <StatusChip status="canonical" label={`${catalogSummary.linkedCount || 0} linked`} size="sm" />
              </div>
            )}
            {catalogError && (
              <div className="pds-muted" style={{ fontSize: 12, color: "var(--pds-red, #e74c3c)" }}>
                {catalogError}
              </div>
            )}
          </div>
          <div style={{ display: "grid", gap: 10 }}>
            {catalogLoading && patientCatalog.length === 0 ? (
              <div className="pds-card" style={{ padding: "18px 16px", color: "var(--pharma-text-muted, #94a3b8)" }}>
                Loading patient registry...
              </div>
            ) : patientCatalog.length > 0 ? (
              patientCatalog.map((patient) => {
                const patientCancer = patient?.primaryCancer?.code || patient?.primaryCancer?.text || "Cancer pending";
                const stageGroup = patient?.oncologySummary?.stageGroup || patient?.oncologySummary?.stageBand || "Stage review";
                const isSelected = selectedPatientRef === patient.patientRef;
                return (
                  <button
                    key={patient.patientRef}
                    type="button"
                    className="pds-card"
                    onClick={() => loadPatientWorkspace(patient.patientRef)}
                    style={{
                      padding: "14px 16px",
                      textAlign: "left",
                      border: isSelected ? "1px solid var(--pds-accent, #4a6cf7)" : "1px solid var(--pharma-border, #e2e8f0)",
                      background: isSelected ? "var(--pds-accent-bg, #eef1f8)" : "var(--pharma-card-bg, #fff)",
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--pharma-text, #0f172a)" }}>
                          {patient.displayName || patient.patientRef}
                        </div>
                        <div className="pds-muted" style={{ fontSize: 12, marginTop: 4 }}>
                          {patient.patientRef}
                        </div>
                      </div>
                      <StatusChip
                        status={patient?.runtimeReadiness?.runnable ? "met" : "unknown"}
                        label={patient?.runtimeReadiness?.runnable ? "Runnable" : "Needs review"}
                        size="sm"
                      />
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
                      <StatusChip
                        status={patient?.linkageStatus === "linked" ? "met" : patient?.linkageStatus === "conflict" ? "conflict" : "unknown"}
                        label={`Linkage: ${patient?.linkageStatus || "unknown"}`}
                        size="sm"
                      />
                      <StatusChip status="source" label={patientCancer} size="sm" />
                      <StatusChip status="canonical" label={stageGroup} size="sm" />
                      <StatusChip status={patient?.gapCount > 0 ? "manual" : "met"} label={`${patient?.gapCount || 0} gaps`} size="sm" />
                    </div>
                  </button>
                );
              })
            ) : (
              <div className="pds-card" style={{ padding: "18px 16px", color: "var(--pharma-text-muted, #94a3b8)" }}>
                No product patient workspaces matched this filter.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ---- Error State -------------------------------------------------- */}
      {error && (
        <div className="pds-card pds-card--error" style={{ borderLeft: "4px solid var(--pds-red, #e74c3c)", padding: "12px 16px" }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* ---- Loading State ------------------------------------------------ */}
      {loading && !isPatientLoaded && (
        <div className="pds-card" style={{ textAlign: "center", padding: "40px 16px", color: "var(--pds-muted, #888)" }}>
          Loading patient context...
        </div>
      )}

      {/* ---- Patient Summary Strip ---------------------------------------- */}
      {isPatientLoaded && patientSummary && (
        <div className="pds-card" style={{ padding: "18px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 4 }}>
            Step 2
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 800, margin: 0, color: "var(--pharma-text, #0f172a)" }}>Review the curated patient summary</h3>
          <div className="pds-muted" style={{ fontSize: 13, marginTop: 6, marginBottom: 14, lineHeight: 1.5 }}>
            This is the high-level checkpoint before you dive deeper into raw record details, resolved evidence, or match readiness.
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
            <div
              style={{
                width: 42, height: 42, borderRadius: "50%",
                background: "var(--pds-accent-bg, #eef1f8)", display: "flex",
                alignItems: "center", justifyContent: "center",
                fontWeight: 700, fontSize: 16, color: "var(--pds-accent, #4a6cf7)",
                flexShrink: 0,
              }}
            >
              {patientSummary.initials || "\uD83D\uDC64"}
            </div>
            <div style={{ minWidth: 0 }}>
              <strong style={{ fontSize: 15 }}>{patientSummary.name}</strong>
              {(patientSummary.age != null || patientSummary.sex) && (
                <span style={{ marginLeft: 8, color: "var(--pds-muted, #888)" }}>
                  {[patientSummary.age != null ? `${patientSummary.age}y` : null, patientSummary.sex].filter(Boolean).join(" / ")}
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginLeft: "auto" }}>
              {/* Cancer type — show as unknown if missing */}
              <StatusChip
                status={patientSummary.cancerType && patientSummary.cancerType !== "unknown" ? "source" : "unknown"}
                label={patientSummary.cancerType && patientSummary.cancerType !== "unknown" ? patientSummary.cancerType : "Cancer: Unknown"}
                size="sm"
              />
              {/* ER — always show, even if unknown */}
              <StatusChip
                status={featureStatusForChip(patientSummary.er)}
                label={`ER: ${patientSummary.er || "unknown"}`}
                size="sm"
              />
              {/* HER2 — always show, even if unknown */}
              <StatusChip
                status={featureStatusForChip(patientSummary.her2)}
                label={`HER2: ${patientSummary.her2 || "unknown"}`}
                size="sm"
              />
              {/* Metastatic — always show */}
              <StatusChip
                status={
                  patientSummary.metastatic === "present" ? "not_met"
                  : patientSummary.metastatic === "absent" ? "met"
                  : "unknown"
                }
                label={
                  patientSummary.metastatic === "present" ? "Metastatic"
                  : patientSummary.metastatic === "absent" ? "Non-metastatic"
                  : "Metastatic: Unknown"
                }
                size="sm"
              />
              {/* Linkage */}
              <StatusChip
                status={linkageStatus === "linked" ? "met" : (linkageStatus === "conflict" ? "conflict" : "unknown")}
                label={linkageStatus === "linked" ? "Linked" : (linkageStatus === "conflict" ? "Linkage Conflict" : "Unlinked")}
                size="sm"
              />
              {/* Runtime readiness */}
              <StatusChip
                status={runtimeReadiness.runnable ? "met" : "not_met"}
                label={runtimeReadiness.runnable ? "Match-Ready" : "Not Match-Ready"}
                size="sm"
              />
            </div>
          </div>
        </div>
      )}

      {/* ---- Tabs --------------------------------------------------------- */}
      {isPatientLoaded && (
        <div className="pds-card" style={{ padding: 18 }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--pharma-text-muted, #94a3b8)", marginBottom: 4 }}>
            Step 3
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 800, margin: 0, color: "var(--pharma-text, #0f172a)" }}>Choose one lens</h3>
          <div className="pds-muted" style={{ fontSize: 13, marginTop: 6, marginBottom: 12, lineHeight: 1.5 }}>
            Open only one layer at a time: the source record, the curated evidence, the match profile, or the current gaps.
          </div>
          <div className="pds-tabs" role="tablist" aria-label="Patient curation tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.key}
              className={`pds-tab ${activeTab === tab.key ? "pds-tab--active" : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
          </div>
        </div>
      )}

      {/* ==================================================================
         TAB A: Health Summary — condensed clinical categories
         ================================================================== */}
      {isPatientLoaded && activeTab === "health" && (() => {
        const ctx = patientContext?.patientContext || {};
        const emr = emrPayload || {};
        const domains = emr.domains || {};
        const resources = emr.resources || [];
        const screening = patientContext?.report || {};
        const conditions = (domains.conditions || resources.filter((r) => (r.resourceType || "").toLowerCase().includes("condition"))).slice(0, 12);
        const medications = (domains.medications || resources.filter((r) => (r.resourceType || "").toLowerCase().includes("medication"))).slice(0, 12);
        const labs = (domains.labs || resources.filter((r) => ["observation", "diagnosticreport"].some((t) => (r.resourceType || "").toLowerCase().includes(t)))).slice(0, 12);
        const procedures = (domains.procedures || resources.filter((r) => (r.resourceType || "").toLowerCase().includes("procedure"))).slice(0, 8);
        const immunizations = (domains.immunizations || resources.filter((r) => (r.resourceType || "").toLowerCase().includes("immunization"))).slice(0, 8);
        const allergies = (domains.allergies || resources.filter((r) => (r.resourceType || "").toLowerCase().includes("allergy"))).slice(0, 8);
        const displayName = ctx.patientName || patientSummary?.name || "Patient";
        const renderItem = (item) => {
          const label = item?.name || item?.title || item?.display || item?.label || item?.value || item?.code?.text || "-";
          const date = item?.effectiveDateTime || item?.onsetDateTime || item?.authoredOn || item?.date || item?.period?.start || "";
          const status = item?.clinicalStatus || item?.verificationStatus || item?.status || "";
          const val = item?.value || item?.valueQuantity ? `${item?.valueQuantity?.value ?? item?.value ?? ""}${item?.valueQuantity?.unit ? ` ${item.valueQuantity.unit}` : ""}` : "";
          return { label: typeof label === "object" ? (label.text || label.display || JSON.stringify(label)) : String(label), date: formatDateShort(date), status: String(status || ""), value: String(val || "") };
        };

        const CATEGORIES = [
          { key: "demographics", title: "Demographics", icon: "👤" },
          { key: "conditions", title: "Diagnoses & Conditions", icon: "🔬", items: conditions },
          { key: "medications", title: "Medications", icon: "💊", items: medications },
          { key: "labs", title: "Labs & Observations", icon: "🧪", items: labs },
          { key: "procedures", title: "Procedures", icon: "🏥", items: procedures },
          { key: "immunizations", title: "Immunizations", icon: "💉", items: immunizations },
          { key: "allergies", title: "Allergies", icon: "⚠️", items: allergies },
          { key: "screening", title: "Screening & Imaging", icon: "📋" },
        ];

        return (
          <div>
            {/* Demographics card */}
            <div className="pds-card" style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <span style={{ fontSize: 18 }}>👤</span>
                <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>Demographics</h3>
              </div>
              <div style={{
                display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
                gap: 10,
              }}>
                {(() => {
                  const ageValue = patientSummary?.age;
                  const ageIsAnomaly = ageValue != null && (ageValue > 100 || ageValue < 0);
                  const cancerValue = patientSummary?.cancerType;
                  const cancerIsUnknown = !cancerValue || cancerValue === "unknown" || cancerValue === "Unknown";
                  const primaryCancerFeature = resolved.primaryCancer || {};
                  return [
                    { label: "Name", value: displayName },
                    {
                      label: "Age",
                      value: ageValue != null ? `${ageValue} years` : "Unknown",
                      tone: ageIsAnomaly ? "conflict" : (ageValue == null ? "unknown" : "normal"),
                      note: ageIsAnomaly ? "Source conflict — synthetic age exceeds plausible range" : null,
                    },
                    {
                      label: "Sex",
                      value: patientSummary?.sex || "Unknown",
                      tone: patientSummary?.sex ? "normal" : "unknown",
                    },
                    {
                      label: "Cancer Type",
                      value: cancerIsUnknown ? "Unknown" : cancerValue,
                      tone: cancerIsUnknown ? "unknown" : "normal",
                      note: cancerIsUnknown
                        ? `Source: ${primaryCancerFeature.sourceOfTruth || "not available"} — ${primaryCancerFeature.reason || "no oncology diagnosis found in linked records"}`
                        : null,
                    },
                    {
                      label: "Linkage",
                      value: linkageStatus === "linked" ? "Linked" : linkageStatus === "conflict" ? "Conflict" : "Unlinked",
                      tone: linkageStatus === "linked" ? "normal" : linkageStatus === "conflict" ? "conflict" : "unknown",
                    },
                    { label: "Records", value: `${resources.length || 0} FHIR resources` },
                  ];
                })().map((item) => (
                  <div key={item.label} style={{
                    padding: "10px 12px", borderRadius: 8,
                    background: item.tone === "conflict" ? "var(--pharma-conflict-bg, #fef2f2)"
                      : item.tone === "unknown" ? "var(--pharma-unknown-bg, #fefce8)"
                      : "var(--pharma-bg, #f8fafc)",
                    borderLeft: item.tone === "conflict" ? "3px solid var(--pharma-conflict, #dc2626)"
                      : item.tone === "unknown" ? "3px solid var(--pharma-unknown, #f59e0b)"
                      : "3px solid transparent",
                  }}>
                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--pharma-text-muted, #94a3b8)" }}>{item.label}</div>
                    <div style={{
                      fontSize: 14, fontWeight: 600, marginTop: 3,
                      color: item.tone === "conflict" ? "var(--pharma-conflict, #dc2626)"
                        : item.tone === "unknown" ? "var(--pharma-unknown, #f59e0b)"
                        : "var(--pharma-text, #0f172a)",
                    }}>
                      {item.value}
                    </div>
                    {item.note && (
                      <div style={{ fontSize: 11, color: "var(--pharma-text-secondary, #475569)", marginTop: 4, lineHeight: 1.4 }}>
                        {item.note}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Clinical categories */}
            {CATEGORIES.filter((cat) => cat.items && cat.items.length > 0).map((cat) => {
              const items = cat.items.map(renderItem);
              return (
                <div className="pds-card" key={cat.key} style={{ marginBottom: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 16 }}>{cat.icon}</span>
                      <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>{cat.title}</h3>
                    </div>
                    <span style={{ fontSize: 12, color: "var(--pharma-text-muted, #94a3b8)", fontWeight: 600 }}>
                      {cat.items.length} record{cat.items.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {items.map((item, i) => (
                      <div key={`${cat.key}-${i}`} style={{
                        display: "flex", alignItems: "center", gap: 10,
                        padding: "8px 12px", borderRadius: 6,
                        background: i % 2 === 0 ? "var(--pharma-bg, #f8fafc)" : "transparent",
                      }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--pharma-text, #0f172a)" }}>{item.label}</div>
                        </div>
                        {item.value && (
                          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--pharma-brand, #00684A)", flexShrink: 0 }}>{item.value}</span>
                        )}
                        {item.status && (
                          <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, background: "var(--pharma-bg, #f8fafc)", border: "1px solid var(--pharma-border, #e2e8f0)", color: "var(--pharma-text-secondary, #475569)", flexShrink: 0 }}>{item.status}</span>
                        )}
                        <span style={{ fontSize: 11, color: "var(--pharma-text-muted, #94a3b8)", flexShrink: 0, minWidth: 80, textAlign: "right" }}>{item.date}</span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}

            {/* Screening & Imaging */}
            {(screening.series?.length > 0 || screening.clinicalNotes || screening.conclusions || screening.biopsyResults || screening.biopsyArtifact) && (
              <div className="pds-card" style={{ marginBottom: 12 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                  <span style={{ fontSize: 16 }}>📋</span>
                  <h3 style={{ fontSize: 15, fontWeight: 700, margin: 0 }}>Screening Report</h3>
                </div>
                <ReportCard
                  patientFullName={screening.user || ctx.patientName || displayName}
                  patientID={
                    screening.patientID
                    || screening["patient-id"]
                    || ctx.patientID
                    || patientContext?.patientLink?.syntheaPatientRef
                    || ""
                  }
                  appointmentID={screening.appointmentID || screening._id || ctx.appointmentID || ""}
                  score={null}
                  conclusions={screening.conclusions || ctx.conclusions || ""}
                  clinicalNotes={screening.clinicalNotes || ctx.clinicalNotes || ""}
                  biopsyResults={screening.biopsyResults || ctx.biopsyResults || null}
                  biopsyArtifact={screening.biopsyArtifact || ctx.biopsyArtifact || null}
                  series={screening.series || []}
                  date={screening.date || screening.reportDate || ""}
                  displayExpandedRow={true}
                  readOnly={true}
                  embedded={true}
                />
              </div>
            )}

            {/* Empty state if no EMR data */}
            {emrLoading && (
              <div className="pds-card" style={{ textAlign: "center", padding: "32px 16px", color: "var(--pharma-text-muted, #94a3b8)" }}>
                <span className="pds-spinner" style={{ marginRight: 8 }} />Loading health records...
              </div>
            )}
            {!emrLoading && resources.length === 0 && !conditions.length && !medications.length && (
              <div className="pds-card" style={{ textAlign: "center", padding: "32px 16px", color: "var(--pharma-text-muted, #94a3b8)" }}>
                No longitudinal health records available for this patient. Screening data may still be present above.
              </div>
            )}
          </div>
        );
      })()}

      {/* ==================================================================
         TAB B: Timeline
         ================================================================== */}
      {isPatientLoaded && activeTab === "timeline" && (
        <div className="pds-card">
          <div className="pds-card__header">
            <h3 className="pds-card__title">Patient Timeline</h3>
            <span style={{ color: "var(--pds-muted, #888)", fontSize: 13 }}>
              {timelineEvents.length} event{timelineEvents.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="pds-card__body">
            {/* Filter chips */}
            <div className="pds-chip-row" style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: 16 }}>
              <button
                type="button"
                className={`pds-chip ${!timelineFilter ? "pds-chip--met" : "pds-chip--neutral"}`}
                onClick={() => setTimelineFilter(null)}
                style={{ cursor: "pointer", border: "none" }}
              >
                All ({timelineEvents.length})
              </button>
              {Object.entries(timelineTypeCounts).map(([type, count]) => (
                <button
                  key={type}
                  type="button"
                  className={`pds-chip ${timelineFilter === type ? "pds-chip--met" : "pds-chip--neutral"}`}
                  onClick={() => setTimelineFilter((prev) => (prev === type ? null : type))}
                  style={{ cursor: "pointer", border: "none" }}
                >
                  {EVENT_TYPE_LABELS[type] || type} ({count})
                </button>
              ))}
            </div>

            {emrLoading ? (
              <div style={{ textAlign: "center", padding: "32px 0", color: "var(--pds-muted, #888)" }}>
                Loading timeline...
              </div>
            ) : monthGroups.length === 0 ? (
              <div style={{ textAlign: "center", padding: "32px 0", color: "var(--pds-muted, #888)" }}>
                No timeline events available for this patient.
              </div>
            ) : (
              monthGroups.map(([monthKey, events], groupIndex) => (
                <details key={`${monthKey}-${groupIndex}`} open={groupIndex < 4}>
                  <summary style={{ fontWeight: 600, fontSize: 14, padding: "8px 0", cursor: "pointer" }}>
                    {monthKey}
                    <span style={{ fontWeight: 400, color: "var(--pds-muted, #888)", marginLeft: 8 }}>
                      {events.length} event{events.length === 1 ? "" : "s"}
                    </span>
                  </summary>
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px", paddingLeft: 12, paddingBottom: 12 }}>
                    {events.map((event, i) => (
                      <div
                        key={`${monthKey}-${i}-${event.reference || event.title}`}
                        style={{
                          display: "flex", alignItems: "flex-start", gap: "10px",
                          padding: "8px 10px", borderRadius: 6,
                          background: "var(--pds-card-nested-bg, #fafbfd)",
                        }}
                      >
                        <span style={{ minWidth: 80, fontSize: 12, color: "var(--pds-muted, #888)", flexShrink: 0 }}>
                          {formatDateShort(event._date)}
                        </span>
                        <StatusChip
                          status={EVENT_TYPE_TONES[event._type] || "neutral"}
                          label={EVENT_TYPE_LABELS[event._type] || event.resourceType || "Event"}
                          size="sm"
                        />
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ fontWeight: 500, fontSize: 13 }}>{event.title || event.display || "-"}</div>
                          {(event.value || event.status) && (
                            <div style={{ fontSize: 12, color: "var(--pds-muted, #888)", marginTop: 2 }}>
                              {[event.value, event.status].filter(Boolean).join(" \u00b7 ")}
                            </div>
                          )}
                        </div>
                        {event.reference && (
                          <span
                            style={{
                              fontSize: 10, padding: "2px 6px", borderRadius: 4,
                              background: "var(--pds-accent-bg, #eef1f8)", color: "var(--pds-accent, #4a6cf7)",
                              flexShrink: 0, whiteSpace: "nowrap",
                            }}
                          >
                            {event.reference}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              ))
            )}
          </div>
        </div>
      )}

      {/* ==================================================================
         TAB C: Curated Evidence
         ================================================================== */}
      {isPatientLoaded && activeTab === "evidence" && (
        <div>
          <div className="pds-card" style={{ marginBottom: 16 }}>
            <div className="pds-card__header">
              <h3 className="pds-card__title">Curated Evidence Profile</h3>
              <span style={{ fontSize: 13, color: "var(--pds-muted, #888)" }}>
                {completeness.coveragePct != null ? `${completeness.coveragePct}% coverage` : ""}
              </span>
            </div>
            <div className="pds-card__body">
              <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--pds-muted, #888)" }}>
                Evidence resolved from heterogeneous EHR sources into canonical match-ready features,
                organized by clinical family.
              </p>
              {completeness.domains && (
                <CriterionStatusBar
                  counts={{
                    met: Object.values(completeness.domains).filter(Boolean).length,
                    unknown: Object.values(completeness.domains).filter((v) => !v).length,
                  }}
                  showLegend
                />
              )}
            </div>
          </div>

          {EVIDENCE_FAMILIES.map((family) => {
            const familyFeatures = family.features
              .map((key) => ({ key, data: resolved[key] }))
              .filter((f) => f.data);
            if (familyFeatures.length === 0 && !["demographics", "primaryCancer", "biomarkers"].includes(family.key)) {
              return null;
            }

            return (
              <div className="pds-card" key={family.key} style={{ marginBottom: 12 }}>
                <div className="pds-card__header">
                  <h4 className="pds-card__title" style={{ fontSize: 14 }}>{family.label}</h4>
                </div>
                <div className="pds-card__body">
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                      gap: "10px",
                    }}
                  >
                    {family.features.map((featureKey) => {
                      const feat = resolved[featureKey] || {};
                      const value = feat.resolvedValue;
                      const status = feat.status || "unknown";
                      const conflict = feat.conflict;
                      const sourceOfTruth = feat.sourceOfTruth;
                      const evidenceRefs = feat.evidenceRefs || [];

                      return (
                        <CriterionCard
                          key={featureKey}
                          status={featureStatusForChip(status)}
                          title={humanizeFeatureKey(featureKey)}
                          reason={resolvedValueDisplay(value)}
                          familyId={family.key}
                          patientValue={resolvedValueDisplay(value)}
                          evaluator={feat.resolutionPolicy || null}
                          evidenceRefs={evidenceRefs.length > 0 ? evidenceRefs : undefined}
                        >
                          <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: 6 }}>
                            <StatusChip status={featureStatusForChip(status)} label={status} size="sm" />
                            {conflict && <StatusChip status="conflict" label="Conflict" size="sm" />}
                            {sourceOfTruth && sourceOfTruth !== "not_available" && (
                              <StatusChip status="source" label={sourceOfTruth.replace(/_/g, " ")} size="sm" />
                            )}
                          </div>
                        </CriterionCard>
                      );
                    })}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ==================================================================
         TAB D: Match Profile
         ================================================================== */}
      {isPatientLoaded && activeTab === "profile" && (
        <div>
          {/* Cohort Membership */}
          <div className="pds-card" style={{ marginBottom: 12 }}>
            <div className="pds-card__header">
              <h3 className="pds-card__title">Patient Registry Status</h3>
            </div>
            <div className="pds-card__body">
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                <StatusChip
                  status="source"
                  label="Unified patient profile"
                />
                <StatusChip
                  status={linkageStatus === "linked" ? "met" : (linkageStatus === "conflict" ? "conflict" : "unknown")}
                  label={`Longitudinal record: ${linkageStatus === "linked" ? "Linked" : linkageStatus === "conflict" ? "Conflict" : "Pending"}`}
                />
                <StatusChip
                  status={hasScreeningContext ? "source" : "unknown"}
                  label={`Screening context: ${hasScreeningContext ? "Attached" : "Not loaded"}`}
                />
                <StatusChip
                  status={runtimeReadiness.benchmarkRunnable ? "met" : "unknown"}
                  label={`Longitudinal lane: ${runtimeReadiness.benchmarkRunnable ? "Ready" : "Needs review"}`}
                />
                <StatusChip
                  status={runtimeReadiness.outreachRunnable ? "met" : "unknown"}
                  label={`Screening-enriched lane: ${runtimeReadiness.outreachRunnable ? "Ready" : "Needs review"}`}
                />
                {cohortMembership.screeningOnly && (
                  <StatusChip
                    status="manual"
                    label="Longitudinal history still pending"
                  />
                )}
              </div>
            </div>
          </div>

          {/* Runtime Readiness */}
          <div className="pds-card" style={{ marginBottom: 12 }}>
            <div className="pds-card__header">
              <h3 className="pds-card__title">Runtime Readiness</h3>
            </div>
            <div className="pds-card__body">
              <ReadinessScorecard
                metrics={[
                  { label: "Runtime Lane", value: runtimeReadiness.mode || "N/A", tone: runtimeReadiness.runnable ? "success" : "warn" },
                  { label: "Runnable", value: runtimeReadiness.runnable ? "Yes" : "No", tone: runtimeReadiness.runnable ? "success" : "danger" },
                  { label: "Linkage", value: linkageStatus, tone: linkageStatus === "linked" ? "success" : (linkageStatus === "conflict" ? "danger" : "warn") },
                  { label: "Critical Conflicts", value: runtimeReadiness.criticalConflictCount ?? 0, tone: (runtimeReadiness.criticalConflictCount || 0) === 0 ? "success" : "danger" },
                  { label: "Longitudinal Lane", value: runtimeReadiness.benchmarkRunnable ? "Ready" : "Needs review", tone: runtimeReadiness.benchmarkRunnable ? "success" : "warn" },
                  { label: "Screening-Enriched Lane", value: runtimeReadiness.outreachRunnable ? "Ready" : "Needs review", tone: runtimeReadiness.outreachRunnable ? "success" : "warn" },
                ]}
              />
              {runtimeReadiness.reasonIfBlocked && (
                <div style={{ marginTop: 10, fontSize: 13, color: "var(--pds-muted, #888)" }}>
                  <strong>Blocked reason:</strong> {runtimeReadiness.reasonIfBlocked.replace(/_/g, " ")}
                </div>
              )}
            </div>
          </div>

          {/* Match Filters */}
          <div className="pds-card" style={{ marginBottom: 12 }}>
            <div className="pds-card__header">
              <h3 className="pds-card__title">Match Filters</h3>
            </div>
            <div className="pds-card__body">
              <div className="pds-chip-row" style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: 12 }}>
                {matchFilters.primaryCancerCode && (
                  <StatusChip status="canonical" label={`Cancer: ${matchFilters.primaryCancerCode}`} size="sm" />
                )}
                {matchFilters.erStatus && (
                  <StatusChip status={featureStatusForChip(matchFilters.erStatus)} label={`ER: ${matchFilters.erStatus}`} size="sm" />
                )}
                {matchFilters.her2Status && (
                  <StatusChip status={featureStatusForChip(matchFilters.her2Status)} label={`HER2: ${matchFilters.her2Status}`} size="sm" />
                )}
                {matchFilters.metastaticStatus && (
                  <StatusChip
                    status={matchFilters.metastaticStatus === "present" ? "not_met" : (matchFilters.metastaticStatus === "absent" ? "met" : "unknown")}
                    label={`Metastatic: ${matchFilters.metastaticStatus}`}
                    size="sm"
                  />
                )}
                {matchFilters.performanceECOG != null && (
                  <StatusChip status="canonical" label={`ECOG: ${matchFilters.performanceECOG}`} size="sm" />
                )}
                {matchFilters.cnsMetastasesStatus && (
                  <StatusChip
                    status={matchFilters.cnsMetastasesStatus === "absent" ? "met" : (matchFilters.cnsMetastasesStatus === "present" ? "not_met" : "unknown")}
                    label={`CNS Mets: ${matchFilters.cnsMetastasesStatus}`}
                    size="sm"
                  />
                )}
              </div>

              <CoverageMeter
                coverage={{
                  hasRules: true,
                  totalRules: Object.keys(completeness.domains || {}).length || 8,
                  evaluatedRules: Object.values(completeness.domains || {}).filter(Boolean).length,
                  unknownRules: Object.values(completeness.domains || {}).filter((v) => !v).length,
                  deterministicCoveragePct: completeness.coveragePct || 0,
                  llmCoveragePct: 0,
                  unknownPct: completeness.coveragePct != null ? (100 - completeness.coveragePct) : 100,
                }}
                emptyLabel="No evidence coverage data available."
              />

              <p style={{ margin: "12px 0 0", fontSize: 13, color: "var(--pds-muted, #888)" }}>
                This is the subset and semantic structure used for matching.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ==================================================================
         TAB E: Gaps & Identity
         ================================================================== */}
      {isPatientLoaded && activeTab === "gaps" && (
        <div>
          {/* Identity & Linkage */}
          <div className="pds-card" style={{ marginBottom: 12 }}>
            <div className="pds-card__header">
              <h3 className="pds-card__title">Identity & Linkage</h3>
            </div>
            <div className="pds-card__body">
              <div style={{ marginBottom: 12 }}>
                <StatusChip
                  status={linkageStatus === "linked" ? "met" : (linkageStatus === "conflict" ? "conflict" : "unknown")}
                  label={`Linkage: ${linkageStatus}`}
                />
              </div>
              {linkageStatus === "conflict" && (identityBlock.linkageConflicts || []).length > 0 && (
                <div>
                  <p style={{ fontSize: 13, color: "var(--pds-muted, #888)", margin: "0 0 8px" }}>
                    Screening and longitudinal identities have conflicting values:
                  </p>
                  {identityBlock.linkageConflicts.map((conflict, i) => (
                    <div
                      key={i}
                      style={{
                        padding: "8px 12px", borderRadius: 6, marginBottom: 6,
                        background: "var(--pds-card-nested-bg, #fafbfd)",
                        borderLeft: "3px solid var(--pds-red, #e74c3c)",
                      }}
                    >
                      <strong style={{ fontSize: 13 }}>{humanizeFeatureKey(conflict.field)}</strong>
                      <div style={{ display: "flex", gap: "8px", marginTop: 4 }}>
                        <StatusChip status="source" label={`Longitudinal: ${conflict.syntheaValue}`} size="sm" />
                        <StatusChip status="conflict" label={`Screening: ${conflict.screeningValue}`} size="sm" />
                      </div>
                      <div style={{ fontSize: 12, color: "var(--pds-muted, #888)", marginTop: 4 }}>
                        {conflict.reason}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {linkageStatus !== "conflict" && (
                <p style={{ fontSize: 13, color: "var(--pds-muted, #888)", margin: 0 }}>
                  {linkageStatus === "linked"
                    ? "Patient identity is linked between screening and longitudinal EHR with no conflicts."
                    : "Patient has not yet been linked to a longitudinal EHR record."
                  }
                </p>
              )}
            </div>
          </div>

          {/* Missing Evidence */}
          <div className="pds-card" style={{ marginBottom: 12 }}>
            <div className="pds-card__header">
              <h3 className="pds-card__title">Missing Evidence</h3>
              <span style={{ fontSize: 13, color: "var(--pds-muted, #888)" }}>
                {gaps.length} gap{gaps.length === 1 ? "" : "s"}
              </span>
            </div>
            <div className="pds-card__body">
              {gaps.length === 0 ? (
                <p style={{ color: "var(--pds-muted, #888)", fontSize: 13, margin: 0 }}>
                  No evidence gaps detected. All key clinical domains are populated.
                </p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  {gaps.map((gap, i) => {
                    const isFillable = gap.fillableBy === "questionnaire_or_site_ehr"
                      || gap.fillableBy === "questionnaire_only"
                      || (gap.fillableBy || "").includes("questionnaire");
                    return (
                      <div
                        key={i}
                        style={{
                          display: "flex", alignItems: "center", gap: "10px",
                          padding: "10px 12px", borderRadius: 6,
                          background: "var(--pds-card-nested-bg, #fafbfd)",
                        }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <strong style={{ fontSize: 13 }}>{humanizeFeatureKey(gap.feature)}</strong>
                          <div style={{ fontSize: 12, color: "var(--pds-muted, #888)", marginTop: 2 }}>
                            {gap.reason}
                          </div>
                        </div>
                        <StatusChip
                          status={isFillable ? "fillable" : "unknown"}
                          label={isFillable ? "Fillable" : (gap.fillableBy || "Site EHR").replace(/_/g, " ")}
                          size="sm"
                        />
                        {isFillable && (
                          <span className="pds-muted" style={{ fontSize: 12 }}>
                            Questionnaire workflow planned
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Readiness Checklist */}
          <div className="pds-card" style={{ marginBottom: 12 }}>
            <div className="pds-card__header">
              <h3 className="pds-card__title">Readiness Checklist</h3>
            </div>
            <div className="pds-card__body">
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {readinessChecklist.map((item, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex", alignItems: "center", gap: "10px",
                      padding: "8px 12px", borderRadius: 6,
                      background: "var(--pds-card-nested-bg, #fafbfd)",
                    }}
                  >
                    <span style={{
                      width: 22, height: 22, borderRadius: "50%",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 13, fontWeight: 700, flexShrink: 0,
                      background: item.passed
                        ? "var(--pds-green-bg, #e8f5e9)"
                        : "var(--pds-red-bg, #fdecea)",
                      color: item.passed
                        ? "var(--pds-green, #43a047)"
                        : "var(--pds-red, #e74c3c)",
                    }}>
                      {item.passed ? "\u2713" : "\u2717"}
                    </span>
                    <span style={{ fontSize: 13, fontWeight: 500 }}>{item.label}</span>
                    <StatusChip
                      status={item.passed ? "met" : "not_met"}
                      label={item.passed ? "Pass" : "Fail"}
                      size="sm"
                    />
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Transformation Arrow */}
          <SectionDivider label="Data Flow" />
          <div style={{ margin: "16px 0" }}>
            <TransformationArrow
              steps={[
                { id: "ehr", label: "Raw EHR", active: activeTab === "record" || activeTab === "timeline" },
                { id: "evidence", label: "Curated Evidence", active: activeTab === "evidence" },
                { id: "profile", label: "Match Profile", active: activeTab === "profile" },
              ]}
            />
          </div>

          {/* Continue to Matching */}
          <div style={{ textAlign: "center", marginTop: 20 }}>
            <button
              className="pds-btn pds-btn--primary"
              onClick={() =>
                navigateSpace("matching", {
                  direction: "patient_to_trials",
                  launchSource: "patient_curation",
                  populationMode: runtimeReadiness.benchmarkRunnable
                    ? "authoritative_oncology"
                    : (runtimeReadiness.outreachRunnable ? "pharmacy_prescreen" : "authoritative_oncology"),
                  patientRef: selectedPatientRef || patientContext?.patientRef || patientContext?.patientLink?.syntheaPatientRef || "",
                })
              }
            >
              Continue to Matching &rarr;
            </button>
          </div>
        </div>
      )}

      {/* ---- Empty state when no patient loaded -------------------------- */}
      {!isPatientLoaded && !loading && !error && (
        <div className="pds-card" style={{ textAlign: "center", padding: "48px 16px", color: "var(--pds-muted, #888)" }}>
          <p style={{ fontSize: 15, margin: "0 0 8px" }}>No patient loaded</p>
          <p style={{ fontSize: 13, margin: 0 }}>
            Search the patient registry or load a random workspace patient to begin exploring the curation pipeline.
          </p>
        </div>
      )}
    </div>
  );
}
