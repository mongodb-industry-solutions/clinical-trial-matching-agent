const normalizeText = (value) => String(value || "").replace(/\s+/g, " ").trim();

const normalizeList = (value) => {
  if (!Array.isArray(value)) return [];
  return value.filter(Boolean);
};

const formatStructuredValue = (key, value) => {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.filter(Boolean).join(", ");
  }
  if (typeof value !== "object") return String(value);

  if (key === "receptorStatus") {
    return [
      value.hr ? `HR ${String(value.hr).toUpperCase()}` : null,
      value.er ? `ER ${String(value.er).toUpperCase()}` : null,
      value.pr ? `PR ${String(value.pr).toUpperCase()}` : null,
      value.her2 ? `HER2 ${String(value.her2).toUpperCase()}` : null,
    ].filter(Boolean).join(" · ");
  }

  if (key === "performanceStatus") {
    if (value.scale && value.value != null) {
      return `${value.scale} ${value.value}`;
    }
    if (value.value != null) {
      return String(value.value);
    }
  }

  if (key === "stageOrExtent") {
    return [
      value.stage ? String(value.stage).toUpperCase() : null,
      value.metastatic ? "Metastatic" : null,
      value.locallyAdvanced ? "Locally advanced" : null,
      value.tnm ? `TNM ${value.tnm}` : null,
    ].filter(Boolean).join(" · ");
  }

  if (key === "oncologyTreatmentHistory") {
    if (Array.isArray(value.terms) && value.terms.length > 0) {
      return value.terms.slice(0, 4).join(", ");
    }
    if (value.summary) return String(value.summary);
  }

  return "";
};

const getFactValue = (facts, key) => {
  const fact = ((facts || {})[key] || {});
  return fact.value == null ? null : fact.value;
};

const renderStatus = (status) => {
  const normalized = String(status?.status || "").toLowerCase();
  if (normalized === "ready") {
    return { label: "Ready", tone: "ready" };
  }
  if (normalized === "screening_only_ready") {
    return { label: "Screening context ready", tone: "review" };
  }
  if (normalized === "needs_linkage" || normalized === "ambiguous_linkage") {
    return { label: "Needs linkage", tone: "review" };
  }
  if (normalized === "insufficient_leafy_data") {
    return { label: "Limited context", tone: "limited" };
  }
  return { label: "Context loaded", tone: "neutral" };
};

const getPreferredPatientRef = (patientContext, emrPayload, patientLink) => normalizeText(
  patientContext?.syntheaPatientRef
  || patientLink?.syntheaPatientRef
  || emrPayload?.patientRef
  || emrPayload?.patient?.patientRef
  || patientContext?.patientId
  || patientContext?.appointmentId
  || patientContext?.reportId
);

const buildAnonymousPatientAlias = (patientContext, emrPayload, patientLink) => {
  const patientRef = getPreferredPatientRef(patientContext, emrPayload, patientLink);
  const compactId = patientRef.split("/").pop() || "";
  const suffix = compactId.replace(/[^a-zA-Z0-9]/g, "").slice(-6).toUpperCase();
  return suffix ? `Patient ${suffix}` : "Selected patient";
};

const redactPatientNameFromText = (value, patientContext, alias) => {
  const text = normalizeText(value);
  if (!text) return "";
  const knownName = normalizeText(patientContext?.patientName);
  if (!knownName || knownName.toLowerCase() === "unknown") return text;
  const parts = knownName.split(" ").filter(Boolean);
  let next = text.replaceAll(knownName, alias);
  if (parts.length > 1) {
    const family = parts[parts.length - 1];
    if (family) {
      next = next.replaceAll(family, alias);
    }
  }
  return next;
};

const sanitizeReferenceDisplay = (referenceObj, alias) => {
  if (!referenceObj || typeof referenceObj !== "object") return referenceObj;
  const next = { ...referenceObj };
  if (next.display) next.display = alias;
  return next;
};

const sanitizeResourceForDisplay = (resource, alias) => {
  if (!resource || typeof resource !== "object") return resource;
  let next;
  try {
    next = JSON.parse(JSON.stringify(resource));
  } catch (error) {
    next = { ...resource };
  }
  if (next.resourceType === "Patient") {
    next.name = [{ text: alias, given: [alias] }];
  }
  if (next.subject) next.subject = sanitizeReferenceDisplay(next.subject, alias);
  if (next.patient) next.patient = sanitizeReferenceDisplay(next.patient, alias);
  if (next.beneficiary) next.beneficiary = sanitizeReferenceDisplay(next.beneficiary, alias);
  return next;
};

const buildScreeningSeries = (reportDoc) => {
  const series = Array.isArray(reportDoc?.series) ? reportDoc.series : [];
  return series.map((entry, index) => {
    const metadata = entry?.imageMetadata || {};
    const base64Preview = metadata.Base64Image || metadata.base64Image || "";
    const thumbnailSrc = base64Preview ? `data:image/jpeg;base64,${base64Preview}` : "";
    const acquiredAt = metadata.AcquisitionDateTime || metadata.StudyDate || metadata.ContentDate || "";
    return {
      id: metadata.SOPInstanceUID || metadata.SeriesInstanceUID || `screening-${index}`,
      laterality: metadata.ImageLaterality || metadata.Laterality || "",
      viewPosition: metadata.ViewPosition || "",
      acquiredAt,
      thumbnailSrc,
      fileURL: metadata.fileURL || "",
    };
  });
};

export const CLINICAL_RECORD_EVENT_LABELS = {
  condition: "Conditions",
  medication: "Medications",
  lab: "Labs",
  encounter: "Encounters",
  procedure: "Procedures",
  immunization: "Immunizations",
  allergy: "Allergies",
  other: "Other",
};

export const classifyClinicalRecordEventType = (resourceType) => {
  const rt = String(resourceType || "").toLowerCase();
  if (rt.includes("condition")) return "condition";
  if (rt.includes("medication")) return "medication";
  if (rt.includes("observation")) return "lab";
  if (rt.includes("encounter")) return "encounter";
  if (rt.includes("procedure")) return "procedure";
  if (rt.includes("immunization")) return "immunization";
  if (rt.includes("allergy")) return "allergy";
  return "other";
};

export const buildPatientClinicalRecord = ({
  patientContext,
  patientContextStatus,
  evidencePack,
  reportDoc,
  appointmentDoc,
  patientLink,
  emrPayload,
}) => {
  const context = patientContext && typeof patientContext === "object" ? patientContext : {};
  const emr = emrPayload && typeof emrPayload === "object" ? emrPayload : {};
  const report = reportDoc && typeof reportDoc === "object" ? reportDoc : {};
  const appointment = appointmentDoc && typeof appointmentDoc === "object" ? appointmentDoc : {};
  const facts = evidencePack?.facts || {};
  const status = renderStatus(patientContextStatus);
  const displayName = buildAnonymousPatientAlias(context, emr, patientLink);
  const screeningSeries = buildScreeningSeries(report);
  const keywords = normalizeList(context.keywords);
  const sanitizedPatient = emr.patient && typeof emr.patient === "object"
    ? { ...emr.patient, name: displayName }
    : {};
  const sanitizedResources = Array.isArray(emr.resources)
    ? emr.resources.map((resource) => sanitizeResourceForDisplay(resource, displayName))
    : [];
  const structuredHighlights = [
    { key: "receptorStatus", label: "Receptor status" },
    { key: "performanceStatus", label: "Performance" },
    { key: "stageOrExtent", label: "Stage / extent" },
    { key: "oncologyTreatmentHistory", label: "Oncology treatment" },
    { key: "latestBmi", label: "Latest BMI" },
  ].map((item) => {
    const value = formatStructuredValue(item.key, getFactValue(facts, item.key));
    return value ? { ...item, value } : null;
  }).filter(Boolean);

  return {
    status,
    identity: {
      displayName,
      age: context.age ?? null,
      sex: context.sex || "",
      patientId: context.patientId || "",
      appointmentId: context.appointmentId || "",
      reportId: context.reportId || String(report?._id || ""),
      syntheaPatientRef: context.syntheaPatientRef || patientLink?.syntheaPatientRef || emr.patientRef || "",
      center: context.center || (((appointment.resource || {}).location || {}).display) || "",
      appointmentStatus: context.appointmentStatus || (((appointment.resource || {}).status) || ""),
      linkStrategy: patientLink?.linkStrategy || "",
      linkageStatus: patientLink?.linkageStatus || "",
      anonymousLabel: displayName,
    },
    screening: {
      present: Boolean(
        reportDoc
        || context.clinicalNotes
        || context.conclusions
        || screeningSeries.length
        || normalizeList(context.biradsValues).length
      ),
      biradsValues: normalizeList(context.biradsValues),
      numberImages: Number(context.numberImages || screeningSeries.length || 0),
      reportDate: context.reportDate || report?.date || "",
      keywords,
      clinicalNotes: redactPatientNameFromText(context.clinicalNotes || report.clinicalNotes || "", context, displayName),
      conclusions: redactPatientNameFromText(context.conclusions || report.conclusions || "", context, displayName),
      series: screeningSeries,
    },
    evidence: {
      structuredHighlights,
    },
    emr: {
      patientRef: emr.patientRef || context.syntheaPatientRef || "",
      patient: sanitizedPatient,
      summary: emr.summary || {},
      timeline: Array.isArray(emr.timeline) ? emr.timeline : [],
      domains: emr.domains && typeof emr.domains === "object" ? emr.domains : {},
      resources: sanitizedResources,
    },
    raw: {
      patientContext: context,
      patientContextStatus,
      evidencePack,
      report,
      appointment,
      patientLink,
      emr,
    },
  };
};
