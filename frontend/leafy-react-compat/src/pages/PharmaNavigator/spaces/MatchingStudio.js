import React, { useState, useCallback, useEffect, useMemo, useRef } from "react";
import {
  StatusChip, ReadinessScorecard, CriterionCard,
  CriterionStatusBar, FunnelVisualization, SectionDivider, ObjectCard,
} from "../../../components/PharmaDesignSystem/PharmaDesignSystem";
import CriterionTreeView from "../../../components/PharmaMatching/CriterionTreeView";
import ClinicalTrialCanonicalWorkspace from "../../../components/ClinicalTrialCanonicalWorkspace/ClinicalTrialCanonicalWorkspace";
import {
  AgenticProgressTicker,
  CandidateEvaluationPanel,
  DeterministicEvidencePanel,
  EvidencePackSummary,
  RuleEvaluationTable,
} from "../../../components/PharmaMatching";
import {
  APIMatchClinicalTrialsForPatient, APIMatchClinicalTrialToPatients,
  APIMatchClinicalTrialsForPatientStream, APIMatchClinicalTrialToPatientsStream,
  APIListClinicalTrials, APIGetClinicalTrialCanonicalTree,
  APIGetRandomPharmaPatientContext,
  APIListProductPatients, APIGetProductPatientWorkspace,
  APIGetClinicalTrial, APIGetClinicalTrialsHealth,
} from "../../../services/APIService";

/* ---------------------------------------------------------------------------
   Helper functions
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

const MATCHING_SERVICE_UNAVAILABLE_COPY = "Clinical trials sidecar unavailable. Start npm --prefix app run start:clinical-trials-service and reload this studio.";
const STAGE_TARGET_WINDOW_LABELS = {
  stage_ii_iii_or_locally_advanced: "Stage II/III or Locally Advanced",
  advanced_or_metastatic: "Advanced or Metastatic",
  mixed_or_manual: "Mixed / Manual Stage Review",
  not_stage_specific: "Not Stage-Specific",
};
const TRIAL_CATALOG_SCOPE_OPTIONS = [
  { value: "", label: "All Runnable Breast Trials" },
  { value: "stage_ii_iii_or_locally_advanced", label: "Stage II/III or Locally Advanced" },
  { value: "advanced_or_metastatic", label: "Advanced or Metastatic" },
  { value: "mixed_or_manual", label: "Mixed / Manual Stage Review" },
  { value: "not_stage_specific", label: "Not Stage-Specific" },
];
const PHARMA_REVIEW_FAMILY_IDS = new Set([
  "therapy.prior_exposure.prohibited_history",
  "organ_function.lab_panel",
  "concomitant_illness.significant",
  "context.infection_recent_systemic_therapy",
  "context.pregnancy_breastfeeding",
  "performance.ecog.max",
  "manual.safety_contraindication_history",
  "manual.prior_exposure_contextual",
  "manual.complex_disease_setting",
]);
const PHARMA_LANE_META = {
  priority_shortlist: {
    label: "Priority Shortlist",
    shortLabel: "Priority",
    status: "candidate",
    description: "Clinically matched with no active pharmacy blocker in the current evidence set.",
  },
  pharmacy_review: {
    label: "Needs Pharmacy Review",
    shortLabel: "Review",
    status: "manual_review_required",
    description: "A pharmacist should validate medication, lab, or chart-based criteria before shortlisting.",
  },
  patient_confirmation: {
    label: "Needs Patient Pre-Screen",
    shortLabel: "Pre-Screen",
    status: "fillable_by_questionnaire",
    description: "Patient-confirmable facts are still needed before moving into sponsor-facing discussion.",
  },
  not_ready_now: {
    label: "Not Ready Now",
    shortLabel: "Not Ready",
    status: "excluded",
    description: "The clinical screen still has active blockers, so this patient should not advance yet.",
  },
};
const PHARMA_FAMILY_ACTIONS = {
  "therapy.prior_exposure.prohibited_history": "Review prior therapy exposure and any required washout before shortlisting.",
  "organ_function.lab_panel": "Validate the latest organ function labs against protocol thresholds.",
  "concomitant_illness.significant": "Review concomitant illness, contraindications, and medication complexity with pharmacy.",
  "context.infection_recent_systemic_therapy": "Confirm infection status and timing of recent systemic therapy.",
  "context.pregnancy_breastfeeding": "Confirm pregnancy, breastfeeding, and contraception requirements with the patient.",
  "performance.ecog.max": "Confirm current performance status before sponsor-facing shortlist.",
  "manual.safety_contraindication_history": "Review safety contraindication history before advancing this candidate.",
  "manual.prior_exposure_contextual": "Review contextual prior-exposure history with pharmacy.",
  "manual.complex_disease_setting": "Validate the disease setting with the study team before shortlisting.",
};
const QUESTIONNAIRE_ACTIONS = {
  pregnancy: "Capture pregnancy and contraception answers during the pre-screen call.",
  activeInfection: "Confirm active infection history with the patient or chart.",
  performanceStatus: "Confirm current performance status during pre-screen.",
  visitAvailability: "Confirm visit availability and travel burden with the patient.",
  transport: "Check transport and support needs before scheduling pre-screening.",
  digitalAccess: "Confirm the patient's preferred follow-up channel and digital access.",
  medicationAdherence: "Discuss adherence barriers before enrollment planning.",
};
const GAP_REASON_LABELS = {
  manualReview: "Manual chart review required",
  concomitantIllness: "Concomitant illness needs validation",
  performanceStatus: "Performance status needs confirmation",
  pregnancy: "Pregnancy or contraception context missing",
  activeInfection: "Infection context missing",
  recentSystemicTherapy: "Recent therapy timing needs validation",
  therapyPriorExposureHistory: "Prior exposure history needs validation",
  organFunctionLabs: "Latest organ function labs need confirmation",
};

function humanizeFamilyId(familyId) {
  if (!familyId) return "Unknown Criterion";
  if (FAMILY_LABELS[familyId]) return FAMILY_LABELS[familyId];
  const parts = String(familyId).split(".");
  return parts
    .map((p) => p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()))
    .join(" - ");
}

function formatPhaseLabel(value) {
  const text = String(value || "").trim();
  if (!text || text.toLowerCase() === "not specified") return "Not specified";
  return text
    .replaceAll("|", " / ")
    .replaceAll("_", " ")
    .replace(/\bphase\s*(\d)\b/gi, "Phase $1")
    .replace(/\bearly phase\s*1\b/gi, "Early Phase 1")
    .replace(/\bna\b/gi, "N/A");
}

function formatStatusLabel(value) {
  return String(value || "Not specified")
    .replaceAll("_", " ")
    .trim();
}

function formatRuntimeMode(value) {
  return String(value || "canonical_only")
    .replaceAll("_", " ")
    .trim();
}

function normalizeCriterionText(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return text
    .replace(/^\s*(?:[-*\u2022•]+|\d+[.)]|\(\d+\))\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
}

function inferCriterionType(line, currentType = "inclusion") {
  const lower = String(line || "").toLowerCase();
  if (/\b(exclusion criteria|exclude|must not|not eligible|ineligible|cannot|no history|without)\b/.test(lower)) {
    return "exclusion";
  }
  if (/\b(inclusion criteria|eligible|must have|required)\b/.test(lower)) {
    return "inclusion";
  }
  return currentType;
}

function extractCriteriaBuckets(trial) {
  const inclusion = [];
  const exclusion = [];
  const seen = new Set();
  const pushUnique = (type, textValue) => {
    const normalized = normalizeCriterionText(textValue);
    if (!normalized || normalized.length < 4) return;
    const dedupeKey = `${type}::${normalized.toLowerCase()}`;
    if (seen.has(dedupeKey)) return;
    seen.add(dedupeKey);
    if (type === "exclusion") exclusion.push(normalized);
    else inclusion.push(normalized);
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

  if (inclusion.length === 0 && exclusion.length === 0) {
    const rawText = String(trial?.eligibility?.rawCriteria || trial?.eligibility_criteria || "");
    const rawLines = rawText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    let currentType = "inclusion";
    rawLines.forEach((line) => {
      const lower = line.toLowerCase();
      if (lower.startsWith("inclusion criteria")) {
        currentType = "inclusion";
        pushUnique("inclusion", line.replace(/inclusion criteria[:\s-]*/i, ""));
        return;
      }
      if (lower.startsWith("exclusion criteria")) {
        currentType = "exclusion";
        pushUnique("exclusion", line.replace(/exclusion criteria[:\s-]*/i, ""));
        return;
      }
      currentType = inferCriterionType(line, currentType);
      pushUnique(currentType, line);
    });
  }

  return { inclusion, exclusion };
}

function formatSponsorLabel(sponsor) {
  if (!sponsor) return "";
  if (typeof sponsor === "string") return sponsor.trim();
  if (typeof sponsor === "object") {
    const lead = String(sponsor.lead || sponsor.name || "").trim();
    const sponsorClass = String(sponsor.class || sponsor.type || "").trim();
    return [lead, sponsorClass].filter(Boolean).join(" - ");
  }
  return String(sponsor).trim();
}

function formatDecision(overall) {
  const normalized = String(overall || "").toLowerCase();
  const MAP = {
    candidate: { label: "Candidate", status: "candidate" },
    eligible: { label: "Candidate", status: "candidate" },
    likely_eligible: { label: "Candidate", status: "candidate" },
    needs_more_data: { label: "Needs More Data", status: "needs_more_data" },
    needs_data: { label: "Needs More Data", status: "needs_more_data" },
    manual_review_required: { label: "Manual Review", status: "manual_review_required" },
    manual: { label: "Manual Review", status: "manual_review_required" },
    excluded: { label: "Excluded", status: "excluded" },
    not_eligible: { label: "Excluded", status: "excluded" },
    likely_ineligible: { label: "Excluded", status: "excluded" },
  };
  return MAP[normalized] || { label: normalized || "Unknown", status: "unknown" };
}

function normalizeMatchRunPayload(payload) {
  return payload?.run || payload || null;
}

function humanizeStageTargetWindow(value) {
  return STAGE_TARGET_WINDOW_LABELS[String(value || "").trim()] || "";
}

function derivePreferredPopulationMode(matchProfile) {
  const readiness = matchProfile?.runtimeReadiness || {};
  if (readiness.benchmarkRunnable) return "authoritative_oncology";
  if (readiness.outreachRunnable) return "pharmacy_prescreen";
  return "authoritative_oncology";
}

function populationRegistryLabel(value) {
  return String(value || "").trim() === "pharmacy_prescreen"
    ? "Unified patient graph · screening enriched"
    : "Unified patient graph";
}

function populationLaneLabel(value) {
  return String(value || "").trim() === "pharmacy_prescreen"
    ? "screening-enriched lane"
    : "curated longitudinal lane";
}

function derivePatientStageTargetWindow(matchProfile) {
  const membership = matchProfile?.cohortMembership || {};
  const matchFilters = matchProfile?.matchFilters || {};
  if (membership.breastCancerStageIiIii) {
    return "stage_ii_iii_or_locally_advanced";
  }
  if (membership.metastaticBreastCancer) {
    return "advanced_or_metastatic";
  }
  if (matchFilters.diseaseWindow === "stage_ii_iii_or_locally_advanced" || matchFilters.diseaseWindow === "advanced_or_metastatic") {
    return matchFilters.diseaseWindow;
  }
  return null;
}

function getTrialIdentifier(trial) {
  return String(trial?._id || trial?.nct_id || trial?.trialId || trial?.nctId || "").trim();
}

function groupResultsByDecision(results) {
  const buckets = { candidate: [], needs_data: [], manual: [], excluded: [] };
  if (!Array.isArray(results)) return buckets;
  for (const row of results) {
    const d = String(row?.overall || row?.decision || "").toLowerCase();
    if (d === "candidate" || d === "eligible" || d === "likely_eligible") {
      buckets.candidate.push(row);
    } else if (d === "needs_more_data" || d === "needs_data") {
      buckets.needs_data.push(row);
    } else if (d === "manual_review_required" || d === "manual") {
      buckets.manual.push(row);
    } else {
      buckets.excluded.push(row);
    }
  }
  return buckets;
}

function dedupeStrings(values, limit = 6) {
  const seen = new Set();
  const items = [];
  for (const value of values || []) {
    const text = String(value || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    items.push(text);
    if (items.length >= limit) break;
  }
  return items;
}

function humanizeGapFeature(feature) {
  const text = String(feature || "").trim();
  if (!text) return "Missing supporting evidence";
  if (GAP_REASON_LABELS[text]) return GAP_REASON_LABELS[text];
  return text
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function getResultRowKey(row, direction, index = 0) {
  if (direction === "patient_to_trials") {
    return String(row?.trialId || row?.nctId || `trial-${index}`).trim();
  }
  return String(row?.patientRef || row?.patientId || `patient-${index}`).trim();
}

function isClinicalRelevantResult(row) {
  const overall = String(row?.overall || row?.decision || "").toLowerCase();
  return overall && !["excluded", "needs_identity_resolution"].includes(overall);
}

function buildTrialToPatientsClinicalFunnel(matchPayload, results) {
  const registry = matchPayload?.catalogCount || matchPayload?.evaluatedCount || 0;
  const evaluated = matchPayload?.evaluatedCount || registry;
  const clinicallyRelevant = Array.isArray(results)
    ? results.filter((row) => isClinicalRelevantResult(row)).length
    : 0;
  return [
    { label: "Registry", count: registry },
    { filterLabel: "Canonical Trial Contract" },
    { label: "Evaluated", count: evaluated },
    { filterLabel: "Clinical Screen" },
    { label: "Clinically Relevant", count: clinicallyRelevant },
  ];
}

function buildTrialToPatientsClinicalBreakdown(results) {
  const grouped = groupResultsByDecision(results);
  return [
    { label: "Candidate", count: grouped.candidate.length },
    { label: "Needs data", count: grouped.needs_data.length },
    { label: "Manual review", count: grouped.manual.length },
    { label: "Excluded", count: grouped.excluded.length },
  ];
}

function buildPharmaViability(row) {
  const evaluations = [
    ...(Array.isArray(row?.criteria) ? row.criteria : []),
    ...(Array.isArray(row?.prefilter?.evaluations) ? row.prefilter.evaluations : []),
  ];
  const gaps = Array.isArray(row?.gaps) ? row.gaps : [];
  const overall = String(row?.overall || row?.decision || "").toLowerCase();
  const notMetRows = evaluations.filter((ev) => String(ev?.status || "").toLowerCase() === "not_met");
  const manualRows = evaluations.filter((ev) => String(ev?.status || "").toLowerCase() === "manual_review_required");
  const unknownRows = evaluations.filter((ev) => String(ev?.status || "").toLowerCase() === "unknown");
  const fillableRows = evaluations.filter((ev) => String(ev?.status || "").toLowerCase() === "fillable_by_questionnaire");
  const questionnaireGaps = gaps.filter((gap) => String(gap?.fillableBy || "").includes("questionnaire"));
  const chartReviewGaps = gaps.filter((gap) => !String(gap?.fillableBy || "").includes("questionnaire"));
  const pharmacySpecificRows = evaluations.filter((ev) => PHARMA_REVIEW_FAMILY_IDS.has(String(ev?.familyId || "").trim()));
  const pharmacyReviewRows = pharmacySpecificRows.filter((ev) => {
    const status = String(ev?.status || "").toLowerCase();
    return status === "manual_review_required" || status === "unknown";
  });
  const genericReviewRows = manualRows.filter((ev) => !PHARMA_REVIEW_FAMILY_IDS.has(String(ev?.familyId || "").trim()));
  const chartUnknownRows = unknownRows.filter((ev) => String(ev?.status || "").toLowerCase() === "unknown");

  const lane = (() => {
    if (overall === "excluded" || overall === "needs_identity_resolution" || notMetRows.length > 0) {
      return "not_ready_now";
    }
    if (pharmacyReviewRows.length > 0 || genericReviewRows.length > 0 || chartReviewGaps.length > 0 || chartUnknownRows.length > 0) {
      return "pharmacy_review";
    }
    if (fillableRows.length > 0 || questionnaireGaps.length > 0 || overall === "needs_more_data") {
      return "patient_confirmation";
    }
    return "priority_shortlist";
  })();

  const statusMeta = PHARMA_LANE_META[lane] || PHARMA_LANE_META.priority_shortlist;
  const reasonTokens = [];
  if (lane === "not_ready_now") {
    reasonTokens.push(...notMetRows.map((ev) => humanizeFamilyId(ev?.familyId || ev?.ruleId)));
  }
  if (lane === "pharmacy_review") {
    reasonTokens.push(...pharmacyReviewRows.map((ev) => humanizeFamilyId(ev?.familyId || ev?.ruleId)));
    reasonTokens.push(...genericReviewRows.map((ev) => humanizeFamilyId(ev?.familyId || ev?.ruleId)));
    reasonTokens.push(...chartUnknownRows.map((ev) => humanizeFamilyId(ev?.familyId || ev?.ruleId)));
    reasonTokens.push(...chartReviewGaps.map((gap) => humanizeGapFeature(gap?.feature || gap?.name)));
  }
  if (lane === "patient_confirmation") {
    reasonTokens.push(...fillableRows.map((ev) => humanizeFamilyId(ev?.familyId || ev?.ruleId)));
    reasonTokens.push(...questionnaireGaps.map((gap) => humanizeGapFeature(gap?.feature || gap?.name)));
  }
  if (lane === "priority_shortlist") {
    reasonTokens.push("No active pharmacy blocker surfaced in the current evidence set");
    if (row?.evidenceCompletenessPct != null) {
      reasonTokens.push(`Evidence completeness ${row.evidenceCompletenessPct}%`);
    }
  }

  const nextActions = dedupeStrings([
    ...pharmacyReviewRows.map((ev) => PHARMA_FAMILY_ACTIONS[String(ev?.familyId || "").trim()]),
    ...genericReviewRows.map((ev) => PHARMA_FAMILY_ACTIONS[String(ev?.familyId || "").trim()]),
    ...notMetRows.map((ev) => PHARMA_FAMILY_ACTIONS[String(ev?.familyId || "").trim()]),
    ...questionnaireGaps.map((gap) => QUESTIONNAIRE_ACTIONS[String(gap?.feature || "").trim()]),
    ...fillableRows.map((ev) => PHARMA_FAMILY_ACTIONS[String(ev?.familyId || "").trim()]),
    ...(lane === "priority_shortlist"
      ? ["Prepare this patient for pharmacist and coordinator pre-screen."]
      : []),
    ...(lane === "pharmacy_review"
      ? ["Review the chart and protocol-specific pharmacy blockers before shortlisting."]
      : []),
    ...(lane === "patient_confirmation"
      ? ["Run a short patient pre-screen to confirm the missing facts."]
      : []),
    ...(lane === "not_ready_now"
      ? ["Do not shortlist this patient until the clinical blockers change or the chart is updated."]
      : []),
  ], 5);

  const summary = (() => {
    if (lane === "not_ready_now") {
      return "This patient still has active clinical blockers, so the pharmacy team should not advance them into the sponsor-facing shortlist.";
    }
    if (lane === "pharmacy_review") {
      return "The patient is close enough clinically to stay in scope, but pharmacy or chart-based validation is still required before advancing.";
    }
    if (lane === "patient_confirmation") {
      return "The patient remains viable if a short pre-screen confirms the missing patient-reported or questionnaire-fillable facts.";
    }
    return "This patient is ready to move from the clinical screen into a pharmacist and coordinator pre-screen shortlist.";
  })();

  return {
    lane,
    label: statusMeta.label,
    shortLabel: statusMeta.shortLabel,
    status: statusMeta.status,
    description: statusMeta.description,
    summary,
    reasons: dedupeStrings(reasonTokens, 4),
    nextActions,
    counts: {
      notMet: notMetRows.length,
      manual: manualRows.length,
      unknown: unknownRows.length,
      fillable: fillableRows.length,
      questionnaireGaps: questionnaireGaps.length,
      chartReviewGaps: chartReviewGaps.length,
    },
  };
}

function groupResultsByPharmaLane(results) {
  const buckets = {
    priority_shortlist: [],
    pharmacy_review: [],
    patient_confirmation: [],
    not_ready_now: [],
  };
  if (!Array.isArray(results)) return buckets;
  for (const row of results) {
    const lane = row?.pharmaReview?.lane || buildPharmaViability(row).lane;
    if (buckets[lane]) {
      buckets[lane].push(row);
    } else {
      buckets.not_ready_now.push(row);
    }
  }
  return buckets;
}

function buildPharmaFunnelStages(matchPayload, results) {
  const evaluated = matchPayload?.evaluatedCount || matchPayload?.catalogCount || 0;
  const grouped = groupResultsByPharmaLane(results);
  const clinicallyRelevant = Array.isArray(results)
    ? results.filter((row) => isClinicalRelevantResult(row)).length
    : 0;
  return [
    { label: "Registry", count: evaluated },
    { filterLabel: "Clinical Candidate Screen" },
    { label: "Clinically Relevant", count: clinicallyRelevant },
    { filterLabel: "Pharmacy Viability Filter" },
    { label: "Priority Shortlist", count: grouped.priority_shortlist.length },
  ];
}

function buildPharmaBreakdown(results) {
  const grouped = groupResultsByPharmaLane(results);
  return [
    { label: "Need pharmacy review", count: grouped.pharmacy_review.length },
    { label: "Need pre-screen", count: grouped.patient_confirmation.length },
    { label: "Not ready now", count: grouped.not_ready_now.length },
  ];
}

function buildPharmaActionPlan(results) {
  const grouped = groupResultsByPharmaLane(results);
  const plan = [];
  if (grouped.priority_shortlist.length > 0) {
    plan.push({
      title: "Prepare the shortlist",
      detail: `Advance ${grouped.priority_shortlist.length} patients into pharmacist and coordinator pre-screening.`,
    });
  }
  if (grouped.pharmacy_review.length > 0) {
    plan.push({
      title: "Review medication and chart evidence",
      detail: `${grouped.pharmacy_review.length} patients need pharmacist or chart validation before they can be presented as viable.`,
    });
  }
  if (grouped.patient_confirmation.length > 0) {
    plan.push({
      title: "Run a short patient pre-screen",
      detail: `${grouped.patient_confirmation.length} patients need questionnaire or outreach confirmation before shortlisting.`,
    });
  }
  if (grouped.not_ready_now.length > 0) {
    plan.push({
      title: "Document current blockers",
      detail: `${grouped.not_ready_now.length} patients are not ready now; use those blockers to inform sponsor conversation and feasibility feedback.`,
    });
  }
  return plan;
}

function buildFunnelStages(matchPayload) {
  if (!matchPayload) return [];
  const catalog = matchPayload.catalogCount
    || matchPayload.totalTrials
    || matchPayload?.prefilter?.scannedTrialCount
    || matchPayload?.prefilter?.scannedPatientCount
    || 0;
  const evaluated = matchPayload.evaluatedCount || matchPayload.evaluated || catalog;
  const results = Array.isArray(matchPayload.results)
    ? matchPayload.results.length
    : (matchPayload.resultCount || 0);
  return [
    { label: "Catalog", count: catalog },
    { filterLabel: "Hard Prefilter" },
    { label: "Evaluated", count: evaluated },
    { filterLabel: "Criterion Check" },
    { label: "Results", count: results },
  ];
}

function buildAggregateCounts(results) {
  const counts = { met: 0, not_met: 0, unknown: 0, manual: 0, fillable: 0, conflict: 0 };
  if (!Array.isArray(results)) return counts;
  for (const row of results) {
    const evaluations = [
      ...(Array.isArray(row?.criteria) ? row.criteria : []),
      ...(Array.isArray(row?.prefilter?.evaluations) ? row.prefilter.evaluations : []),
    ];
    for (const ev of evaluations) {
      const s = String(ev?.status || "").toLowerCase();
      if (s === "met" || s === "pass") counts.met++;
      else if (s === "not_met" || s === "fail") counts.not_met++;
      else if (s === "manual_review_required" || s === "manual") counts.manual++;
      else if (s === "fillable_by_questionnaire" || s === "fillable") counts.fillable++;
      else if (s === "blocked_by_linkage_conflict" || s === "conflict") counts.conflict++;
      else counts.unknown++;
    }
  }
  return counts;
}

function buildSingleResultCounts(result) {
  const counts = { met: 0, not_met: 0, unknown: 0, manual: 0, fillable: 0, conflict: 0 };
  if (!result) return counts;
  const evaluations = [
    ...(Array.isArray(result?.criteria) ? result.criteria : []),
    ...(Array.isArray(result?.prefilter?.evaluations) ? result.prefilter.evaluations : []),
  ];
  for (const ev of evaluations) {
    const s = String(ev?.status || "").toLowerCase();
    if (s === "met" || s === "pass") counts.met++;
    else if (s === "not_met" || s === "fail") counts.not_met++;
    else if (s === "manual_review_required" || s === "manual") counts.manual++;
    else if (s === "fillable_by_questionnaire" || s === "fillable") counts.fillable++;
    else if (s === "blocked_by_linkage_conflict" || s === "conflict") counts.conflict++;
    else counts.unknown++;
  }
  return counts;
}

function extractPatientChips(patientContext, matchProfile) {
  const chips = [];
  const features = patientContext?.features || patientContext?.patientFeatures || {};
  if (features.er_status) chips.push({ label: `ER ${features.er_status}`, status: features.er_status === "positive" ? "met" : "not_met" });
  if (features.her2_status) chips.push({ label: `HER2 ${features.her2_status}`, status: features.her2_status === "positive" ? "met" : "not_met" });
  if (features.ecog != null) chips.push({ label: `ECOG ${features.ecog}`, status: "source" });
  if (features.cancer_type) chips.push({ label: features.cancer_type, status: "source" });
  if (features.stage) chips.push({ label: `Stage ${features.stage}`, status: "source" });
  if (chips.length === 0 && matchProfile?.cohorts) {
    chips.push({ label: "Unified patient graph", status: "source" });
  }
  return chips;
}

function patientDisplayName(patientContext) {
  const name = patientContext?.patientName || patientContext?.name;
  if (name) return name;
  const ref = patientContext?.patientRef || patientContext?.ref || patientContext?.id;
  return ref ? `Patient ${ref}` : "Unknown Patient";
}

function patientAge(patientContext) {
  return patientContext?.age || patientContext?.features?.age || null;
}

function patientSex(patientContext) {
  return patientContext?.sex || patientContext?.features?.sex || patientContext?.gender || null;
}

function patientInitials(patientContext) {
  const name = patientContext?.patientName || patientContext?.name || "";
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return "PT";
}

const MATCH_STEPS_P2T = [
  "Building patient context",
  "Filtering trial catalog",
  "Applying hard prefilters",
  "Evaluating criteria deterministically",
  "Ranking results by decision",
];

const MATCH_STEPS_T2P = [
  "Loading trial canonical tree",
  "Filtering patient registry",
  "Applying hard prefilters",
  "Evaluating criteria deterministically",
  "Building the clinical candidate pool",
];

const LAUNCH_SOURCE_LABELS = {
  trial_ingestion: "Clinical Trial Ingestion",
  patient_curation: "Patient Data",
  pattern_article: "Pattern Article",
  matching: "Matching Studio",
  showcase: "Showcase Replay",
};

function humanizeLaunchSource(value) {
  return LAUNCH_SOURCE_LABELS[String(value || "").trim()] || "";
}

function extractLinkedPatientRef(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  return normalized.startsWith("Patient/") ? normalized : `Patient/${normalized}`;
}

function extractPatientRefFromIdentity(identity = {}) {
  const sourceRefs = Array.isArray(identity?.sourceRefs) ? identity.sourceRefs : [];
  const directRef =
    identity?.syntheaPatientRef
    || identity?.patientRef
    || identity?.linkedPatientRef
    || identity?.resolvedPatientRef
    || "";
  const sourceRef = sourceRefs.find((item) => String(item || "").trim().startsWith("Patient/")) || "";
  return extractLinkedPatientRef(directRef || sourceRef);
}

function resolveAuthoritativePatientRef(contextPayload, evidencePayload, matchPayload) {
  const context = contextPayload || {};
  const evidenceResponse = evidencePayload || {};
  const matchResponse = matchPayload || {};
  const evidence = evidenceResponse?.patientEvidenceProfile || evidenceResponse || {};
  const matchProfilePayload = matchResponse?.patientMatchProfile || matchResponse || {};
  return extractLinkedPatientRef(
    context?.patientRef
    || context?.ref
    || context?.id
    || context?.patientLink?.syntheaPatientRef
    || context?.patientContext?.syntheaPatientRef
    || context?.patientContext?.patientRef
    || evidenceResponse?.patientRef
    || evidence?.patientRef
    || extractPatientRefFromIdentity(evidence?.identity)
    || matchResponse?.patientRef
    || matchProfilePayload?.patientRef
    || extractPatientRefFromIdentity(matchProfilePayload?.identity)
    || ""
  );
}

/* ---------------------------------------------------------------------------
   MatchingStudio Component
   --------------------------------------------------------------------------- */

export default function MatchingStudio({
  navigateSpace,
  setAppointmentId,
  setPatientId,
  selectedPatientRef,
  setSelectedPatientRef,
  selectedTrialId,
  setSelectedTrialId,
  matchDirection,
  setMatchDirection,
  populationMode: routedPopulationMode,
  setPopulationMode: setRoutedPopulationMode,
  stageTargetWindow: routedStageTargetWindow,
  setStageTargetWindow: setRoutedStageTargetWindow,
  benchmarkPackId,
  showcaseCase,
  launchSource,
}) {
  // ---- Direction and population mode ----
  const [localDirection, setLocalDirection] = useState(matchDirection || "patient_to_trials");
  const [localPopulationMode, setLocalPopulationMode] = useState(routedPopulationMode || "authoritative_oncology");
  const [localCatalogStageTargetWindow, setLocalCatalogStageTargetWindow] = useState(routedStageTargetWindow || "");
  const direction = matchDirection || localDirection;
  const setDirection = setMatchDirection || setLocalDirection;
  const populationMode = routedPopulationMode || localPopulationMode;
  const setPopulationMode = setRoutedPopulationMode || setLocalPopulationMode;

  // ---- Match state ----
  const [matching, setMatching] = useState(false);
  const [matchPayload, setMatchPayload] = useState(null);
  const [selectedResult, setSelectedResult] = useState(null);
  const [matchError, setMatchError] = useState(null);
  const [matchProgress, setMatchProgress] = useState(null);
  const [serviceHealthy, setServiceHealthy] = useState(null);
  const [pharmaFilterApplied, setPharmaFilterApplied] = useState(false);
  const [trialToPatientsLens, setTrialToPatientsLens] = useState("clinical");

  // ---- Patient context ----
  const [patientContext, setPatientContext] = useState(null);
  const [evidenceProfile, setEvidenceProfile] = useState(null);
  const [matchProfile, setMatchProfile] = useState(null);
  const [patientRef, setPatientRef] = useState(null);
  const [patientLoading, setPatientLoading] = useState(false);
  const [patientError, setPatientError] = useState(null);
  const [patientCatalog, setPatientCatalog] = useState([]);
  const [patientCatalogSummary, setPatientCatalogSummary] = useState(null);
  const [patientCatalogLoading, setPatientCatalogLoading] = useState(false);
  const [patientCatalogError, setPatientCatalogError] = useState(null);
  const [patientSearch, setPatientSearch] = useState("");

  // ---- Trial context ----
  const [trialCatalog, setTrialCatalog] = useState({ trials: [], totalCount: 0, page: 1 });
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState(null);
  const [catalogFilters, setCatalogFilters] = useState({ q: "" });
  const [selectedTrial, setSelectedTrial] = useState(null);
  const [treePayload, setTreePayload] = useState(null);
  const [treeLoading, setTreeLoading] = useState(false);

  // ---- Collapsed sections ----
  const [showExcluded, setShowExcluded] = useState(false);
  const [showCanonicalTree, setShowCanonicalTree] = useState(false);
  const [trialWorkspaceTab, setTrialWorkspaceTab] = useState("overview");
  const [trialWorkspaceOpen, setTrialWorkspaceOpen] = useState(false);
  const [loadedContextKey, setLoadedContextKey] = useState("");
  const [loadedTrialKey, setLoadedTrialKey] = useState("");
  const [autoRunKey, setAutoRunKey] = useState("");
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [canvasTab, setCanvasTab] = useState("overview");
  const trialWorkspaceRef = useRef(null);
  const patientStageTargetWindow = useMemo(
    () => derivePatientStageTargetWindow(matchProfile),
    [matchProfile],
  );
  const setCatalogStageTargetWindow = useCallback((nextValue) => {
    const normalizedValue = String(nextValue || "").trim();
    setLocalCatalogStageTargetWindow(normalizedValue);
    if (setRoutedStageTargetWindow) {
      setRoutedStageTargetWindow(normalizedValue);
    }
  }, [setRoutedStageTargetWindow]);
  const catalogStageTargetWindow = direction === "patient_to_trials"
    ? patientStageTargetWindow
    : (routedStageTargetWindow || localCatalogStageTargetWindow || null);

  // ---- Sync external props ----
  useEffect(() => {
    if (matchDirection) {
      setLocalDirection(matchDirection);
    }
  }, [matchDirection]);
  useEffect(() => {
    if (routedPopulationMode) {
      setLocalPopulationMode(routedPopulationMode);
    }
  }, [routedPopulationMode]);
  useEffect(() => {
    setLocalCatalogStageTargetWindow(routedStageTargetWindow || "");
  }, [routedStageTargetWindow]);
  useEffect(() => {
    if (direction !== "trial_to_patients") {
      setPharmaFilterApplied(false);
      setTrialToPatientsLens("clinical");
    }
    setCanvasTab("overview");
  }, [direction]);

  useEffect(() => {
    if (selectedResult) setCanvasTab("criteria");
  }, [selectedResult]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const health = await APIGetClinicalTrialsHealth();
        if (!cancelled) {
          setServiceHealthy(Boolean(health?.ok || health?.healthy || health?.status === "ok"));
        }
      } catch (_error) {
        if (!cancelled) {
          setServiceHealthy(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- Load trial catalog ----
  const loadCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError(null);
    if (serviceHealthy === false) {
      setTrialCatalog({ trials: [], totalCount: 0, page: 1 });
      setCatalogLoading(false);
      setCatalogError(MATCHING_SERVICE_UNAVAILABLE_COPY);
      return;
    }
    try {
      const params = {
        disease_area: "breast_cancer",
        runnable: true,
        limit: 50,
      };
      if (catalogFilters.q) params.search = catalogFilters.q;
      if (catalogStageTargetWindow) params.stage_target_window = catalogStageTargetWindow;
      const data = await APIListClinicalTrials(params);
      setTrialCatalog({
        trials: Array.isArray(data?.trials) ? data.trials : [],
        totalCount: data?.totalCount || data?.total || 0,
        page: data?.page || 1,
      });
    } catch (err) {
      console.error("Failed to load trial catalog", err);
      setTrialCatalog({ trials: [], totalCount: 0, page: 1 });
      setCatalogError(
        serviceHealthy === false
          ? MATCHING_SERVICE_UNAVAILABLE_COPY
          : (err?.response?.data?.detail || err?.response?.data?.error || err?.message || "Failed to load trial catalog")
      );
    } finally {
      setCatalogLoading(false);
    }
  }, [catalogFilters, catalogStageTargetWindow, serviceHealthy]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  const loadPatientCatalog = useCallback(async (searchValue = "") => {
    setPatientCatalogLoading(true);
    setPatientCatalogError(null);
    try {
      const response = await APIListProductPatients({
        limit: 12,
        focus: "breast_cancer",
        search: searchValue || undefined,
      });
      setPatientCatalog(Array.isArray(response?.patients) ? response.patients : []);
      setPatientCatalogSummary(response?.summary || null);
    } catch (err) {
      console.error("Failed to load patient catalog", err);
      setPatientCatalog([]);
      setPatientCatalogSummary(null);
      setPatientCatalogError(
        err?.response?.data?.detail || err?.response?.data?.error || err?.message || "Failed to load patient registry",
      );
    } finally {
      setPatientCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    if (direction !== "patient_to_trials") return undefined;
    const timeoutId = setTimeout(() => {
      loadPatientCatalog(patientSearch);
    }, 180);
    return () => clearTimeout(timeoutId);
  }, [direction, loadPatientCatalog, patientSearch]);

  // ---- Load patient ----
  const loadPatientWorkspace = useCallback(async (patientRefValue, workspaceSeed = null) => {
    const normalizedRef = extractLinkedPatientRef(patientRefValue || workspaceSeed?.patientRef);
    if (!normalizedRef) return;
    setPatientLoading(true);
    setPatientError(null);
    setPatientContext(null);
    setEvidenceProfile(null);
    setMatchProfile(null);
    setPatientRef(normalizedRef);
    setMatchPayload(null);
    setSelectedResult(null);
    if (setAppointmentId) setAppointmentId("");
    if (setPatientId) setPatientId("");
    try {
      const workspace = workspaceSeed || await APIGetProductPatientWorkspace(normalizedRef);
      const nextPatientRef = resolveAuthoritativePatientRef(
        workspace,
        workspace?.patientEvidenceProfile,
        workspace?.patientMatchProfile,
      ) || normalizedRef;
      const patientSummaryPayload = workspace?.patientSummary || {};
      const evidencePayload = workspace?.patientEvidenceProfile || null;
      const nextMatchProfile = workspace?.patientMatchProfile || null;
      setEvidenceProfile(evidencePayload);
      setMatchProfile(nextMatchProfile);
      setPopulationMode(derivePreferredPopulationMode(nextMatchProfile));
      setPatientContext({
        patientRef: nextPatientRef,
        patientSummary: patientSummaryPayload,
        patientName:
          patientSummaryPayload?.displayName
          || evidencePayload?.demographics?.name
          || evidencePayload?.identity?.canonicalPatientKey
          || nextMatchProfile?.identity?.canonicalPatientKey
          || nextPatientRef,
        features: {
          age: evidencePayload?.demographics?.ageYears,
          sex: evidencePayload?.demographics?.sex,
          cancer_type:
            (evidencePayload?.resolvedFeatures?.primaryCancer || {}).resolvedValue
            || patientSummaryPayload?.primaryCancer?.code
            || patientSummaryPayload?.primaryCancer?.text
            || "",
          er_status:
            (evidencePayload?.resolvedFeatures?.ER || {}).resolvedValue
            || patientSummaryPayload?.oncologySummary?.erStatus
            || "",
          her2_status:
            (evidencePayload?.resolvedFeatures?.HER2 || {}).resolvedValue
            || patientSummaryPayload?.oncologySummary?.her2Status
            || "",
          ecog:
            (evidencePayload?.resolvedFeatures?.ECOG || {}).resolvedValue
            || patientSummaryPayload?.oncologySummary?.performanceECOG
            || null,
          stage:
            (evidencePayload?.resolvedFeatures?.stageGroup || {}).resolvedValue
            || patientSummaryPayload?.oncologySummary?.stageGroup
            || patientSummaryPayload?.oncologySummary?.stageBand
            || "",
        },
      });
      setLoadedContextKey(`patientRef::${nextPatientRef}`);
      setPatientRef(nextPatientRef || null);
      if (setSelectedPatientRef && nextPatientRef) setSelectedPatientRef(nextPatientRef);
    } catch (err) {
      console.error("Failed to load patient workspace", err);
      setPatientError(
        err?.response?.data?.detail || err?.response?.data?.error || err?.message || "Failed to load patient workspace",
      );
    } finally {
      setPatientLoading(false);
    }
  }, [setAppointmentId, setPatientId, setSelectedPatientRef]);

  const loadRandomPatient = useCallback(async () => {
    try {
      const workspace = await APIGetRandomPharmaPatientContext();
      const nextPatientRef = resolveAuthoritativePatientRef(
        workspace,
        workspace?.patientEvidenceProfile,
        workspace?.patientMatchProfile,
      );
      if (!nextPatientRef) {
        throw new Error("Random patient selection did not include a patient reference.");
      }
      await loadPatientWorkspace(nextPatientRef, workspace);
    } catch (err) {
      console.error("Failed to load random patient", err);
      setPatientError(
        err?.response?.data?.detail || err?.response?.data?.error || err?.message || "Failed to load random patient",
      );
    }
  }, [loadPatientWorkspace]);

  // ---- Load canonical tree for selected result trial ----
  const loadTreeForTrial = useCallback(async (trialId) => {
    if (!trialId) return;
    setTreeLoading(true);
    try {
      const data = await APIGetClinicalTrialCanonicalTree(trialId);
      setTreePayload(data?.canonical_tree || data?.tree || data || null);
    } catch (err) {
      console.error("Failed to load canonical tree", err);
      setTreePayload(null);
    } finally {
      setTreeLoading(false);
    }
  }, []);

  // ---- Load trial detail for trial-to-patients ----
  const loadTrialDetail = useCallback(async (trialId) => {
    if (!trialId) return;
    setTreeLoading(true);
    setMatchPayload(null);
    setSelectedResult(null);
    setShowExcluded(false);
    setPharmaFilterApplied(false);
    setTrialToPatientsLens("clinical");
    try {
      const [data, treeData] = await Promise.all([
        APIGetClinicalTrial(trialId),
        APIGetClinicalTrialCanonicalTree(trialId).catch(() => null),
      ]);
      setSelectedTrial(data?.trial || data || null);
      setTreePayload(treeData?.canonical_tree || treeData?.tree || treeData || null);
      setLoadedTrialKey(trialId);
      if (setSelectedTrialId) setSelectedTrialId(trialId);
    } catch (err) {
      console.error("Failed to load trial detail", err);
    } finally {
      setTreeLoading(false);
    }
  }, [setSelectedTrialId]);

  useEffect(() => {
    if (!selectedPatientRef || direction !== "patient_to_trials") return;
    const nextContextKey = `patientRef::${selectedPatientRef}`;
    if (nextContextKey !== loadedContextKey) {
      loadPatientWorkspace(selectedPatientRef);
    }
  }, [selectedPatientRef, direction, loadedContextKey, loadPatientWorkspace]);

  useEffect(() => {
    if (!selectedTrialId || direction !== "trial_to_patients") return;
    if (selectedTrialId !== loadedTrialKey) {
      loadTrialDetail(selectedTrialId);
      loadTreeForTrial(selectedTrialId);
    }
  }, [selectedTrialId, direction, loadedTrialKey, loadTrialDetail, loadTreeForTrial]);

  // ---- When a result is selected, load its canonical tree ----
  useEffect(() => {
    if (direction === "patient_to_trials" && selectedResult) {
      const trialId = selectedResult?.trialId || selectedResult?.nctId;
      if (trialId) loadTreeForTrial(trialId);
    }
  }, [direction, selectedResult, loadTreeForTrial]);

  // ---- Run match: Patient to Trials ----
  const runPatientToTrials = useCallback(async () => {
    if (!patientRef) return;
    if (serviceHealthy !== true) {
      setMatchError(MATCHING_SERVICE_UNAVAILABLE_COPY);
      return;
    }
    setMatching(true);
    setMatchError(null);
    setMatchPayload(null);
    setSelectedResult(null);
    setShowExcluded(false);
    setPharmaFilterApplied(false);
    setTrialToPatientsLens("clinical");
    setMatchProgress(null);
    try {
      const payload = {
        populationMode,
        ...(patientStageTargetWindow ? { stageTargetWindow: patientStageTargetWindow } : {}),
        ...(showcaseCase && selectedTrialId ? { trialIds: [selectedTrialId] } : {}),
        ...(benchmarkPackId ? { benchmarkPackId, benchmarkLabel: showcaseCase ? `showcase_${showcaseCase}` : "showcase_replay" } : {}),
        ...(showcaseCase ? { runType: "benchmark_showcase" } : {}),
      };
      for await (const event of APIMatchClinicalTrialsForPatientStream(patientRef, payload)) {
        if (event.type === "error") {
          setMatchError(event.message || "Match failed");
          return;
        }
        if (event.type === "done") {
          const data = normalizeMatchRunPayload(event.run);
          setMatchPayload(data);
          setMatchProgress(null);
          const firstRow = Array.isArray(data?.results) ? data.results[0] : null;
          if (firstRow) setSelectedResult(firstRow);
          if (showcaseCase && selectedTrialId) {
            const matchingRow = (Array.isArray(data?.results) ? data.results : []).find(
              (row) => String(row?.trialId || row?.nctId || "") === String(selectedTrialId)
            );
            if (matchingRow) setSelectedResult(matchingRow);
          }
        } else {
          setMatchProgress(event);
        }
      }
    } catch (err) {
      console.error("Match failed", err);
      setMatchError(err?.message || "Match request failed");
    } finally {
      setMatching(false);
      setMatchProgress(null);
    }
  }, [patientRef, populationMode, selectedTrialId, benchmarkPackId, showcaseCase, serviceHealthy, patientStageTargetWindow]);

  // ---- Run match: Trial to Patients ----
  const runTrialToPatients = useCallback(async () => {
    const trialId = getTrialIdentifier(selectedTrial) || selectedTrialId;
    if (!trialId) return;
    if (serviceHealthy !== true) {
      setMatchError(MATCHING_SERVICE_UNAVAILABLE_COPY);
      return;
    }
    setMatching(true);
    setMatchError(null);
    setMatchPayload(null);
    setSelectedResult(null);
    setShowExcluded(false);
    setPharmaFilterApplied(false);
    setTrialToPatientsLens("clinical");
    setMatchProgress(null);
    try {
      const payload = {
        populationMode,
        ...(showcaseCase && selectedPatientRef ? { patientRefs: [selectedPatientRef] } : {}),
        ...(benchmarkPackId ? { benchmarkPackId, benchmarkLabel: showcaseCase ? `showcase_${showcaseCase}` : "showcase_replay" } : {}),
        ...(showcaseCase ? { runType: "benchmark_showcase" } : {}),
      };
      for await (const event of APIMatchClinicalTrialToPatientsStream(trialId, payload)) {
        if (event.type === "error") {
          setMatchError(event.message || "Match failed");
          return;
        }
        if (event.type === "done") {
          const data = normalizeMatchRunPayload(event.run);
          setMatchPayload(data);
          setMatchProgress(null);
          const firstRow = Array.isArray(data?.results) ? data.results[0] : null;
          if (firstRow) setSelectedResult(firstRow);
          if (showcaseCase && selectedPatientRef) {
            const matchingRow = (Array.isArray(data?.results) ? data.results : []).find(
              (row) => String(row?.patientRef || "") === String(selectedPatientRef)
            );
            if (matchingRow) setSelectedResult(matchingRow);
          }
        } else {
          setMatchProgress(event);
        }
      }
    } catch (err) {
      console.error("Match failed", err);
      setMatchError(err?.message || "Match request failed");
    } finally {
      setMatching(false);
      setMatchProgress(null);
    }
  }, [selectedTrial, selectedTrialId, selectedPatientRef, populationMode, benchmarkPackId, showcaseCase, serviceHealthy]);

  useEffect(() => {
    if (!showcaseCase || !benchmarkPackId) return;
    const currentTrialId = getTrialIdentifier(selectedTrial) || selectedTrialId || "";
    const currentPatientRef = patientRef || selectedPatientRef || "";
    const nextAutoRunKey = [
      showcaseCase,
      benchmarkPackId,
      direction,
      currentTrialId,
      currentPatientRef,
      populationMode,
    ].join("::");
    if (nextAutoRunKey === autoRunKey) return;

    if (direction === "patient_to_trials" && currentPatientRef && currentTrialId && !matching) {
      setAutoRunKey(nextAutoRunKey);
      runPatientToTrials();
      return;
    }

    if (direction === "trial_to_patients" && currentPatientRef && currentTrialId && !matching) {
      setAutoRunKey(nextAutoRunKey);
      runTrialToPatients();
    }
  }, [
    showcaseCase,
    benchmarkPackId,
    direction,
    selectedTrial,
    selectedTrialId,
    patientRef,
    selectedPatientRef,
    populationMode,
    matching,
    autoRunKey,
    runPatientToTrials,
    runTrialToPatients,
  ]);

  // ---- Derived data ----
  const matchResults = useMemo(
    () => {
      const rows = Array.isArray(matchPayload?.results) ? matchPayload.results : [];
      if (direction !== "trial_to_patients") return rows;
      return rows.map((row) => ({
        ...row,
        pharmaReview: buildPharmaViability(row),
      }));
    },
    [matchPayload, direction],
  );

  const groupedResults = useMemo(() => groupResultsByDecision(matchResults), [matchResults]);
  const groupedPharmaResults = useMemo(() => groupResultsByPharmaLane(matchResults), [matchResults]);
  const defaultFunnelStages = useMemo(() => buildFunnelStages(matchPayload), [matchPayload]);
  const clinicalFunnelStages = useMemo(
    () => (direction === "trial_to_patients"
      ? buildTrialToPatientsClinicalFunnel(matchPayload, matchResults)
      : buildFunnelStages(matchPayload)),
    [direction, matchPayload, matchResults],
  );
  const clinicalFunnelBreakdown = useMemo(
    () => (direction === "trial_to_patients"
      ? buildTrialToPatientsClinicalBreakdown(matchResults)
      : []),
    [direction, matchResults],
  );
  const pharmaFunnelStages = useMemo(
    () => buildPharmaFunnelStages(matchPayload, matchResults),
    [matchPayload, matchResults],
  );
  const pharmaFunnelBreakdown = useMemo(
    () => buildPharmaBreakdown(matchResults),
    [matchResults],
  );
  const pharmaActionPlan = useMemo(
    () => buildPharmaActionPlan(matchResults),
    [matchResults],
  );
  const aggregateCounts = useMemo(() => buildAggregateCounts(matchResults), [matchResults]);
  const selectedResultCounts = useMemo(() => buildSingleResultCounts(selectedResult), [selectedResult]);
  const selectedResultPharmaReview = useMemo(
    () => (selectedResult ? (selectedResult?.pharmaReview || buildPharmaViability(selectedResult)) : null),
    [selectedResult],
  );
  const clinicallyRelevantCount = useMemo(
    () => matchResults.filter((row) => isClinicalRelevantResult(row)).length,
    [matchResults],
  );

  const patientChips = useMemo(
    () => extractPatientChips(patientContext, matchProfile),
    [patientContext, matchProfile],
  );

  const patientLoaded = Boolean(patientContext || evidenceProfile || matchProfile);
  const patientReady = Boolean(patientLoaded && patientRef);
  const patientRuntimeReadiness = useMemo(
    () => (matchProfile?.runtimeReadiness || {}),
    [matchProfile],
  );
  const patientLinkageStatus = useMemo(
    () => (
      matchProfile?.identity?.linkageStatus
      || evidenceProfile?.identity?.linkageStatus
      || patientContext?.patientSummary?.linkageStatus
      || patientRuntimeReadiness?.linkageStatus
      || "unknown"
    ),
    [evidenceProfile, matchProfile, patientContext, patientRuntimeReadiness],
  );
  const patientStageScopeLabel = useMemo(
    () => humanizeStageTargetWindow(patientStageTargetWindow),
    [patientStageTargetWindow],
  );
  const catalogStageScopeLabel = useMemo(
    () => humanizeStageTargetWindow(catalogStageTargetWindow),
    [catalogStageTargetWindow],
  );
  const patientBenchmarkRunnable = Boolean(
    patientRuntimeReadiness?.benchmarkRunnable ?? patientRuntimeReadiness?.runnable,
  );
  const patientOutreachRunnable = Boolean(
    patientRuntimeReadiness?.outreachRunnable ?? patientRuntimeReadiness?.runnable,
  );
  const runtimeReady = populationMode === "pharmacy_prescreen"
    ? patientOutreachRunnable
    : patientBenchmarkRunnable;
  const matchingServiceReady = serviceHealthy === true;
  const selectedTrialStageTargetWindow = useMemo(
    () => selectedTrial?.stage_relevance?.targetWindow || selectedTrial?.stageRelevance?.targetWindow || null,
    [selectedTrial],
  );
  const patientGaps = useMemo(() => {
    const gaps = matchProfile?.gaps || evidenceProfile?.gaps || [];
    return Array.isArray(gaps) ? gaps : [];
  }, [matchProfile, evidenceProfile]);

  // ---- Trial readiness metrics (for trial-to-patients) ----
  const trialReadinessMetrics = useMemo(() => {
    if (!selectedTrial) return [];
    const readiness = selectedTrial.runtimeReadiness || selectedTrial.readiness || selectedTrial.readinessScorecard || {};
    const summary = selectedTrial.criteria_summary || selectedTrial.criteriaSummary || {};
    return [
      {
        label: "Criteria Parsed",
        value: summary.total_nodes ?? summary.totalNodes ?? "-",
        tone: (summary.total_nodes ?? summary.totalNodes ?? 0) > 0 ? "positive" : "neutral",
      },
      {
        label: "Deterministic",
        value: readiness.deterministicPatientImpactingCount ?? summary.deterministic_rules ?? summary.deterministicRules ?? "-",
        tone: "neutral",
      },
      {
        label: "Manual",
        value: readiness.manualPatientImpactingCount ?? summary.manual_review_rules ?? summary.manualReviewRules ?? "-",
        tone: "neutral",
      },
      {
        label: "Runtime Ready",
        value: readiness.runnable ? "Yes" : "No",
        tone: readiness.runnable ? "positive" : "caution",
      },
    ];
  }, [selectedTrial]);

  const launchSourceLabel = useMemo(
    () => humanizeLaunchSource(showcaseCase ? "showcase" : launchSource),
    [launchSource, showcaseCase],
  );
  const hasLockedScope = Boolean(
    launchSource
    || showcaseCase
    || benchmarkPackId
    || selectedTrialId
    || selectedPatientRef
    || patientRef
  );

  const scopeSummary = useMemo(() => {
    const rows = [];
    const currentTrialId = getTrialIdentifier(selectedTrial) || selectedTrialId || "";
    const currentPatientRef = patientRef || selectedPatientRef || "";
    if (direction === "patient_to_trials" && currentPatientRef) {
      rows.push({ label: "Patient", value: currentPatientRef, status: "neutral" });
    }
    if (direction === "trial_to_patients" && currentTrialId) {
      rows.push({ label: "Trial", value: currentTrialId, status: "neutral" });
    }
    if (direction === "trial_to_patients" && selectedTrialStageTargetWindow) {
      rows.push({ label: "Trial Window", value: humanizeStageTargetWindow(selectedTrialStageTargetWindow), status: "source" });
    }
    if (showcaseCase && direction === "patient_to_trials" && currentTrialId) {
      rows.push({ label: "Scoped Trial", value: currentTrialId, status: "canonical" });
    }
    if (showcaseCase && direction === "trial_to_patients" && currentPatientRef) {
      rows.push({ label: "Scoped Patient", value: currentPatientRef, status: "canonical" });
    }
    return rows;
  }, [direction, patientRef, selectedPatientRef, selectedTrial, selectedTrialId, selectedTrialStageTargetWindow, showcaseCase]);

  const selectedTrialCriteria = useMemo(
    () => extractCriteriaBuckets(selectedTrial),
    [selectedTrial],
  );
  const selectedTrialSummary = useMemo(
    () => String(selectedTrial?.summary || selectedTrial?.brief_summary || selectedTrial?.briefSummary || "").trim(),
    [selectedTrial],
  );
  const selectedTrialSites = useMemo(() => {
    const siteCoverage = Array.isArray(selectedTrial?.siteCoverage)
      ? selectedTrial.siteCoverage
      : Array.isArray(selectedTrial?.contacts_locations)
        ? selectedTrial.contacts_locations
        : Array.isArray(selectedTrial?.sites)
          ? selectedTrial.sites
          : [];
    return siteCoverage;
  }, [selectedTrial]);
  const selectedTrialSourceUrl = useMemo(
    () => selectedTrial?.sourceUrl || selectedTrial?.source_url || selectedTrial?.study_url || selectedTrial?.url || "",
    [selectedTrial],
  );
  const selectedTrialSponsorLabel = useMemo(
    () => formatSponsorLabel(selectedTrial?.sponsor),
    [selectedTrial],
  );
  const selectedTrialRuntimeReadiness = useMemo(
    () => selectedTrial?.runtimeReadiness || selectedTrial?.readiness || selectedTrial?.readinessScorecard || {},
    [selectedTrial],
  );

  useEffect(() => {
    setTrialWorkspaceTab("overview");
    if (selectedTrial) {
      setTrialWorkspaceOpen(true);
    }
  }, [selectedTrial?._id, selectedTrial?.trialId, selectedTrial?.nct_id, selectedTrial?.nctId]);

  const openTrialWorkspace = useCallback((tab = "overview") => {
    setTrialWorkspaceTab(tab);
    setTrialWorkspaceOpen(true);
  }, []);

  const clearMatchingScope = useCallback(() => {
    setMatchPayload(null);
    setSelectedResult(null);
    setMatchError(null);
    setShowExcluded(false);
    setShowCanonicalTree(false);
    setPharmaFilterApplied(false);
    setTrialToPatientsLens("clinical");
    setPatientContext(null);
    setEvidenceProfile(null);
    setMatchProfile(null);
    setPatientRef(null);
    setPatientError(null);
    setSelectedTrial(null);
    setTreePayload(null);
    setLoadedContextKey("");
    setLoadedTrialKey("");
    setAutoRunKey("");
    setPatientCatalogError(null);
    setPatientSearch("");
    if (setAppointmentId) setAppointmentId("");
    if (setPatientId) setPatientId("");
    navigateSpace("matching", {
      direction,
      populationMode,
    });
    if (setRoutedStageTargetWindow) {
      setRoutedStageTargetWindow("");
    }
    setLocalCatalogStageTargetWindow("");
  }, [direction, navigateSpace, populationMode, setAppointmentId, setPatientId, setRoutedStageTargetWindow]);
  const applyPharmaFilter = useCallback(() => {
    setPharmaFilterApplied(true);
    setTrialToPatientsLens("pharmacy");
    if (selectedResult) return;
    const priorityRow = groupedPharmaResults.priority_shortlist[0]
      || groupedPharmaResults.pharmacy_review[0]
      || groupedPharmaResults.patient_confirmation[0]
      || groupedPharmaResults.not_ready_now[0]
      || null;
    if (priorityRow) {
      setSelectedResult(priorityRow);
    }
  }, [groupedPharmaResults, selectedResult]);

  /* =========================================================================
     Render helpers
     ========================================================================= */

  const renderGuidedStart = () => {
    if (hasLockedScope) return null;
    const isTrialToPatients = direction === "trial_to_patients";
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, padding: "8px 0", borderBottom: "1px solid #e2e8f0", flexWrap: "wrap" }}>
        <span className="pds-muted" style={{ fontSize: 12, flex: 1 }}>
          {isTrialToPatients
            ? "Select a canonical trial contract from the panel, run the clinical candidate screen, and then apply the pharmacy viability filter."
            : "Load a linked patient from the panel, then run Canonical Match."}
          {serviceHealthy === false && <span style={{ color: "#dc2626", marginLeft: 8 }}>Clinical trials service unavailable.</span>}
        </span>
        <button
          type="button"
          className="pds-btn pds-btn--ghost"
          style={{ fontSize: 12, padding: "4px 10px" }}
          onClick={() => navigateSpace && navigateSpace(isTrialToPatients ? "ingestion" : "patient", { launchSource: "matching" })}
        >
          {isTrialToPatients ? "Clinical Trial Ingestion →" : "Patient Data →"}
        </button>
      </div>
    );
  };

  // ---- Top Controls ----
  const renderTopControls = () => {
    const seg = (active) => ({
      fontSize: 12, padding: "4px 12px",
      background: active ? "#0f172a" : "transparent",
      color: active ? "#fff" : "#475569",
      cursor: "pointer", fontWeight: active ? 600 : 400,
      border: "none", transition: "background 0.1s",
    });
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 20, marginBottom: 14, padding: "8px 0", borderBottom: "1px solid #e2e8f0", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "#94a3b8" }}>Direction</span>
          <div style={{ display: "flex", borderRadius: 4, overflow: "hidden", border: "1px solid #cbd5e1" }}>
            <button type="button" style={{ ...seg(direction === "patient_to_trials"), borderRight: "1px solid #cbd5e1" }}
              onClick={() => { setDirection("patient_to_trials"); setMatchPayload(null); setSelectedResult(null); }}>
              Patient → Trials
            </button>
            <button type="button" style={seg(direction === "trial_to_patients")}
              onClick={() => { setDirection("trial_to_patients"); setMatchPayload(null); setSelectedResult(null); }}>
              Trial → Patients
            </button>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "#94a3b8" }}>Patient Registry</span>
          <span className="pds-muted" style={{ fontSize: 12 }}>
            {populationRegistryLabel(populationMode)}
          </span>
        </div>
      </div>
    );
  };

  // ---- Patient Selector (left panel - patient_to_trials) ----
  const renderPatientSelector = () => (
    <div>
      <div className="pds-field" style={{ marginBottom: 10 }}>
        <label className="pds-field__label">Search the patient registry</label>
        <input
          className="pds-field__input"
          type="text"
          value={patientSearch}
          onChange={(e) => setPatientSearch(e.target.value)}
          placeholder="Patient ref, ER, HER2, stage"
        />
      </div>
      <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6, marginBottom: 12 }}>
        Choose a product patient workspace, then inspect every inclusion and exclusion check against the selected trial set.
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="pds-btn pds-btn--secondary"
          disabled={patientLoading}
          onClick={loadRandomPatient}
        >
          Random Patient
        </button>
        {patientRef && (
          <button
            type="button"
            className="pds-btn pds-btn--ghost"
            disabled={patientLoading}
            onClick={() => loadPatientWorkspace(patientRef)}
          >
            Reload Current
          </button>
        )}
      </div>
      {patientCatalogSummary && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 12 }}>
          <StatusChip status="source" label={`${patientCatalogSummary.totalCount || 0} patients`} size="sm" />
          <StatusChip status="met" label={`${patientCatalogSummary.runnableCount || 0} runnable`} size="sm" />
          <StatusChip status="canonical" label={`${patientCatalogSummary.linkedCount || 0} linked`} size="sm" />
        </div>
      )}
      {patientCatalogError && (
        <div className="pds-muted" style={{ fontSize: 12, color: "#dc2626", marginTop: 10 }}>
          {patientCatalogError}
        </div>
      )}
      <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
        {patientCatalogLoading && patientCatalog.length === 0 ? (
          <div className="pds-card" style={{ padding: 12, color: "var(--pharma-text-muted, #94a3b8)" }}>
            Loading patient registry...
          </div>
        ) : patientCatalog.length > 0 ? (
          patientCatalog.map((patient) => {
            const patientCancer = patient?.primaryCancer?.code || patient?.primaryCancer?.text || "Cancer pending";
            const stageGroup = patient?.oncologySummary?.stageGroup || patient?.oncologySummary?.stageBand || "Stage review";
            const isSelected = (patientRef || selectedPatientRef) === patient.patientRef;
            return (
              <button
                key={patient.patientRef}
                type="button"
                className="pds-card"
                onClick={() => loadPatientWorkspace(patient.patientRef)}
                style={{
                  padding: 12,
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
          <div className="pds-card" style={{ padding: 12, color: "var(--pharma-text-muted, #94a3b8)" }}>
            No product patient workspaces matched this filter.
          </div>
        )}
      </div>
      <div
        className="pds-card"
        style={{
          marginTop: 12,
          padding: 12,
          background: "var(--pharma-bg, #f8fafc)",
          border: "1px solid var(--pharma-border, #e2e8f0)",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--pharma-text, #0f172a)", marginBottom: 4 }}>
          Need the guided patient workflow?
        </div>
        <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6, marginBottom: 10 }}>
          Patient Data shows linkage status, curated evidence, gaps, and runtime readiness before you run matching.
        </div>
        <button
          type="button"
          className="pds-btn pds-btn--secondary"
          onClick={() => navigateSpace && navigateSpace("patient", {
            launchSource: "matching",
            patientRef: patientRef || selectedPatientRef || "",
          })}
        >
          Open Patient Data &rarr;
        </button>
      </div>
    </div>
  );

  // ---- Loaded patient display (left panel - patient_to_trials) ----
  const renderPatientLoaded = () => {
    const age = patientAge(patientContext);
    const sex = patientSex(patientContext);
    const subtitle = [age && `${age}y`, sex].filter(Boolean).join(" / ") || "Patient";

    return (
      <div>
        <ObjectCard
          avatar={patientInitials(patientContext)}
          title={patientDisplayName(patientContext)}
          subtitle={subtitle}
          chips={patientChips}
          selected
        />

        <SectionDivider label="Match Profile" />

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
          <StatusChip
            status={patientLinkageStatus === "linked" ? "met" : patientLinkageStatus === "conflict" ? "conflict" : "unknown"}
            label={`Linkage: ${patientLinkageStatus || "pending"}`}
            size="sm"
          />
          <StatusChip
            status={runtimeReady ? "met" : "not_met"}
            label={runtimeReady
              ? `Ready in ${populationLaneLabel(populationMode)}`
              : `Blocked in ${populationLaneLabel(populationMode)}`}
            size="sm"
          />
          <StatusChip
            status={patientBenchmarkRunnable ? "met" : "unknown"}
            label={`Longitudinal-ready: ${patientBenchmarkRunnable ? "Yes" : "No"}`}
            size="sm"
          />
          <StatusChip
            status={patientOutreachRunnable ? "met" : "unknown"}
            label={`Screening-ready: ${patientOutreachRunnable ? "Yes" : "No"}`}
            size="sm"
          />
        </div>

        {patientGaps.length > 0 && (
          <>
            <SectionDivider label="Gaps" />
            <div style={{ fontSize: 13 }}>
              {patientGaps.map((gap, i) => {
                const feature = typeof gap === "string" ? gap : gap?.feature || gap?.name || "Unknown";
                const questionnaireFillable = typeof gap === "object" && String(gap?.fillableBy || "").includes("questionnaire");
                const gapStatus = typeof gap === "object" ? (questionnaireFillable || gap?.fillable ? "fillable" : (gap?.status || "unknown")) : "unknown";
                return (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                    <StatusChip status={gapStatus} size="sm" />
                    <span>{feature}</span>
                  </div>
                );
              })}
            </div>
          </>
        )}

        <div style={{ marginTop: 12 }}>
          <button
            type="button"
            className="pds-btn pds-btn--ghost"
            onClick={() => navigateSpace && navigateSpace("patient", {
              launchSource: "matching",
              patientRef: patientRef || selectedPatientRef || "",
            })}
          >
            Open Patient Data &rarr;
          </button>
        </div>
      </div>
    );
  };

  // ---- Result ObjectCards (reusable for both directions) ----
  const renderResultCard = (row, index) => {
    const isP2T = direction === "patient_to_trials";
    const id = isP2T
      ? (row.trialId || row.nctId || `trial-${index}`)
      : (row.patientRef || row.patientId || `patient-${index}`);
    const title = isP2T
      ? (row.trialTitle || row.title || id)
      : (row.patientName || row.name || id);
    const subtitle = isP2T
      ? [row.nctId, row.phase].filter(Boolean).join(" - ")
      : [row.age && `${row.age}y`, row.sex].filter(Boolean).join(" / ");
    const avatar = isP2T
      ? (row.nctId || id).slice(0, 3).toUpperCase()
      : (row.patientName || row.name || "PT").slice(0, 2).toUpperCase();
    const decision = formatDecision(row.overall || row.decision);
    const counts = buildSingleResultCounts(row);
    const pharmaReview = !isP2T ? (row?.pharmaReview || buildPharmaViability(row)) : null;
    const isSelected = Boolean(selectedResult)
      && getResultRowKey(selectedResult, direction) === getResultRowKey(row, direction, index);
    const evidencePct = row?.evidenceCompletenessPct != null ? `${row.evidenceCompletenessPct}% evidence` : "";
    const chips = [
      { label: `Clinical: ${decision.label}`, status: decision.status },
      ...(!isP2T && pharmaReview
        ? [{ label: `Pharma: ${pharmaReview.shortLabel}`, status: pharmaReview.status }]
        : []),
      ...(evidencePct ? [{ label: evidencePct, status: "source" }] : []),
    ];

    return (
      <ObjectCard
        key={id + "-" + index}
        avatar={avatar}
        title={title}
        subtitle={subtitle}
        chips={chips}
        statusBar={counts}
        selected={isSelected}
        onClick={() => setSelectedResult(row)}
      />
    );
  };

  const renderPharmaSummaryCard = (pharmaReview, { compact = false } = {}) => {
    if (!pharmaReview) return null;
    return (
      <div className={`pharma-viability-detail ${compact ? "compact" : ""}`.trim()}>
        <div className="pharma-viability-detail__header">
          <StatusChip status={pharmaReview.status} label={pharmaReview.label} size="sm" />
          <span className="pds-muted" style={{ fontSize: 12 }}>{pharmaReview.description}</span>
        </div>
        <div className="pharma-viability-detail__summary">
          {pharmaReview.summary}
        </div>
        {pharmaReview.reasons.length > 0 ? (
          <div className="pharma-evidence-chip-row" style={{ marginTop: 10 }}>
            {pharmaReview.reasons.map((reason) => (
              <span key={reason} className={`pharma-evidence-chip ${
                pharmaReview.lane === "priority_shortlist"
                  ? "pass"
                  : pharmaReview.lane === "not_ready_now"
                    ? "blocked"
                    : "review"
              }`}>
                {reason}
              </span>
            ))}
          </div>
        ) : null}
        {pharmaReview.nextActions.length > 0 ? (
          <div className="pharma-viability-detail__actions">
            <div className="pds-muted" style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>
              Next Actions
            </div>
            <div className="pharma-viability-list">
              {pharmaReview.nextActions.map((action) => (
                <div key={action} className="pharma-viability-list__item">
                  <span className="pharma-viability-list__dot" />
                  <span>{action}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    );
  };

  const renderTrialToPatientsStageBoard = () => (
    <div className="pharma-viability-board">
      <div className="pharma-viability-stage-card is-complete">
        <div className="pharma-viability-stage-card__eyebrow">Step 1</div>
        <div className="pharma-viability-stage-card__title">Find Clinical Candidates</div>
        <div className="pharma-viability-stage-card__text">
          Screen the patient registry against the published trial contract and keep the clinically relevant pool visible.
        </div>
        <div className="pharma-viability-stage-card__metrics">
          <div>
            <span>Registry</span>
            <strong>{matchPayload?.evaluatedCount ?? matchPayload?.catalogCount ?? 0}</strong>
          </div>
          <div>
            <span>Clinically relevant</span>
            <strong>{clinicallyRelevantCount}</strong>
          </div>
        </div>
        <div className="pharma-viability-stage-card__footer">
          <StatusChip status="candidate" label="Clinical screen complete" size="sm" />
        </div>
      </div>

      <div className={`pharma-viability-stage-card ${pharmaFilterApplied ? "is-active" : "is-awaiting"}`.trim()}>
        <div className="pharma-viability-stage-card__eyebrow">Step 2</div>
        <div className="pharma-viability-stage-card__title">Run Pharmacy Viability Filter</div>
        <div className="pharma-viability-stage-card__text">
          Convert the candidate pool into a shortlist that is easier to discuss with pharmacy, coordinators, and sponsors.
        </div>
        <div className="pharma-viability-stage-card__metrics">
          <div>
            <span>Priority shortlist</span>
            <strong>{groupedPharmaResults.priority_shortlist.length}</strong>
          </div>
          <div>
            <span>Need review</span>
            <strong>{groupedPharmaResults.pharmacy_review.length + groupedPharmaResults.patient_confirmation.length}</strong>
          </div>
        </div>
        <div className="pharma-viability-stage-card__footer">
          {!pharmaFilterApplied ? (
            <button
              type="button"
              className="pds-btn pds-btn--primary"
              style={{ fontSize: 12, padding: "6px 12px" }}
              onClick={applyPharmaFilter}
            >
              Run Pharmacy Viability Filter
            </button>
          ) : (
            <div className="pharma-viability-lens-toggle">
              <button
                type="button"
                className={`pharma-viability-lens-toggle__btn ${trialToPatientsLens === "clinical" ? "active" : ""}`.trim()}
                onClick={() => setTrialToPatientsLens("clinical")}
              >
                Clinical View
              </button>
              <button
                type="button"
                className={`pharma-viability-lens-toggle__btn ${trialToPatientsLens === "pharmacy" ? "active" : ""}`.trim()}
                onClick={() => setTrialToPatientsLens("pharmacy")}
              >
                Pharmacy View
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  const renderTrialToPatientsResultsList = () => {
    if (trialToPatientsLens === "pharmacy" && pharmaFilterApplied) {
      const hiddenCount = groupedPharmaResults.not_ready_now.length;
      return (
        <div>
          {groupedPharmaResults.priority_shortlist.length > 0 ? (
            <>
              <SectionDivider label={`Priority Shortlist (${groupedPharmaResults.priority_shortlist.length})`} />
              {groupedPharmaResults.priority_shortlist.map((row, index) => renderResultCard(row, index))}
            </>
          ) : null}

          {groupedPharmaResults.pharmacy_review.length > 0 ? (
            <>
              <SectionDivider label={`Needs Pharmacy Review (${groupedPharmaResults.pharmacy_review.length})`} />
              {groupedPharmaResults.pharmacy_review.map((row, index) => renderResultCard(row, groupedPharmaResults.priority_shortlist.length + index))}
            </>
          ) : null}

          {groupedPharmaResults.patient_confirmation.length > 0 ? (
            <>
              <SectionDivider label={`Needs Patient Pre-Screen (${groupedPharmaResults.patient_confirmation.length})`} />
              {groupedPharmaResults.patient_confirmation.map((row, index) => renderResultCard(row, groupedPharmaResults.priority_shortlist.length + groupedPharmaResults.pharmacy_review.length + index))}
            </>
          ) : null}

          {hiddenCount > 0 ? (
            <>
              <div style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="pds-btn pds-btn--ghost"
                  onClick={() => setShowExcluded((prev) => !prev)}
                >
                  {showExcluded ? "Hide" : "Show"} {hiddenCount} not-ready patient{hiddenCount !== 1 ? "s" : ""}
                </button>
              </div>
              {showExcluded ? groupedPharmaResults.not_ready_now.map((row, index) => renderResultCard(
                row,
                groupedPharmaResults.priority_shortlist.length
                + groupedPharmaResults.pharmacy_review.length
                + groupedPharmaResults.patient_confirmation.length
                + index,
              )) : null}
            </>
          ) : null}
        </div>
      );
    }

    return (
      <div>
        {groupedResults.candidate.length > 0 && (
          <>
            <SectionDivider label={`Candidates (${groupedResults.candidate.length})`} />
            {groupedResults.candidate.map((row, i) => renderResultCard(row, i))}
          </>
        )}

        {groupedResults.needs_data.length > 0 && (
          <>
            <SectionDivider label={`Needs More Data (${groupedResults.needs_data.length})`} />
            {groupedResults.needs_data.map((row, i) => renderResultCard(row, i + groupedResults.candidate.length))}
          </>
        )}

        {groupedResults.manual.length > 0 && (
          <>
            <SectionDivider label={`Manual Review (${groupedResults.manual.length})`} />
            {groupedResults.manual.map((row, i) => renderResultCard(row, i + groupedResults.candidate.length + groupedResults.needs_data.length))}
          </>
        )}

        {groupedResults.excluded.length > 0 && (
          <>
            <div style={{ marginTop: 12 }}>
              <button
                type="button"
                className="pds-btn pds-btn--ghost"
                onClick={() => setShowExcluded((prev) => !prev)}
              >
                {showExcluded ? "Hide" : "Show"} {groupedResults.excluded.length} excluded patient{groupedResults.excluded.length !== 1 ? "s" : ""}
              </button>
            </div>
            {showExcluded && groupedResults.excluded.map((row, i) => renderResultCard(
              row,
              i + groupedResults.candidate.length + groupedResults.needs_data.length + groupedResults.manual.length,
            ))}
          </>
        )}
      </div>
    );
  };

  // ---- Center panel: Pre-run, Running, Post-run (patient_to_trials) ----
  const renderCenterP2T = () => {
    // --- Running ---
    if (matching) {
      return (
        <div>
          <AgenticProgressTicker
            active
            title="Running canonical match"
            subtitle={`Registry: ${populationRegistryLabel(populationMode)}`}
            steps={MATCH_STEPS_P2T}
          />
        </div>
      );
    }

    // --- Post-run ---
    if (matchPayload && !matching) {
      return (
        <div>
          <FunnelVisualization stages={defaultFunnelStages} />

          <div style={{ margin: "12px 0" }}>
            <CriterionStatusBar counts={aggregateCounts} />
          </div>

          {matchError && (
            <div className="pds-card" style={{ padding: 12, marginBottom: 12, color: "#dc2626" }}>
              Match error: {matchError}
            </div>
          )}

          {groupedResults.candidate.length > 0 && (
            <>
              <SectionDivider label={`Candidates (${groupedResults.candidate.length})`} />
              {groupedResults.candidate.map((row, i) => renderResultCard(row, i))}
            </>
          )}

          {groupedResults.needs_data.length > 0 && (
            <>
              <SectionDivider label={`Needs More Data (${groupedResults.needs_data.length})`} />
              {groupedResults.needs_data.map((row, i) => renderResultCard(row, i))}
            </>
          )}

          {groupedResults.manual.length > 0 && (
            <>
              <SectionDivider label={`Manual Review (${groupedResults.manual.length})`} />
              {groupedResults.manual.map((row, i) => renderResultCard(row, i))}
            </>
          )}

          {groupedResults.excluded.length > 0 && (
            <>
              <div style={{ marginTop: 12 }}>
                <button
                  type="button"
                  className="pds-btn pds-btn--ghost"
                  onClick={() => setShowExcluded((prev) => !prev)}
                >
                  {showExcluded ? "Hide" : "Show"} {groupedResults.excluded.length} excluded trial{groupedResults.excluded.length !== 1 ? "s" : ""}
                </button>
              </div>
              {showExcluded && groupedResults.excluded.map((row, i) => renderResultCard(row, i))}
            </>
          )}

          <div style={{ marginTop: 16 }}>
            <button
              type="button"
              className="pds-btn pds-btn--secondary"
              onClick={runPatientToTrials}
              disabled={!patientReady}
            >
              Re-run Match
            </button>
          </div>
        </div>
      );
    }

    // --- Pre-run ---
    return (
      <div>
        <div
          style={{
            padding: 14,
            borderRadius: 12,
            border: "1px solid var(--pharma-border, #e2e8f0)",
            background: "var(--pharma-bg, #f8fafc)",
            marginBottom: 16,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--pharma-text, #0f172a)", marginBottom: 6 }}>
            Current run
          </div>
          <div className="pds-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
            {patientReady
              ? `This run will screen ${trialCatalog.totalCount || 0} ${patientStageScopeLabel ? `${patientStageScopeLabel.toLowerCase()} breast trial contracts` : "runtime-ready canonical trials"} for the loaded patient using the ${populationLaneLabel(populationMode)} of the unified patient graph.`
              : patientLoaded
                ? "This patient still needs a linked longitudinal record before patient-to-trials matching can run. Resolve identity and evidence in Patient Data first."
                : "Load one patient first, then run a deterministic screen across the published canonical trial corpus."}
          </div>
          {patientStageScopeLabel ? (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>
              Mixed/manual stage contracts remain in scope so the demo still traverses every rule.
            </div>
          ) : null}
        </div>

        <button
          type="button"
          className="pds-btn pds-btn--primary"
          style={{ width: "100%", marginBottom: 16, padding: "12px 24px", fontSize: 15 }}
          disabled={!patientReady || !runtimeReady || matching || !matchingServiceReady}
          onClick={runPatientToTrials}
        >
          Run Canonical Match
        </button>

        {serviceHealthy !== true ? (
          <div className="pds-muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 16 }}>
            {serviceHealthy === null ? "Checking clinical trials sidecar readiness..." : MATCHING_SERVICE_UNAVAILABLE_COPY}
          </div>
        ) : null}

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <StatusChip
            status={patientReady ? "met" : patientLoaded ? "not_met" : "unknown"}
            label={patientReady ? "Patient Graph Loaded" : patientLoaded ? "Patient Needs Linkage" : "Patient Pending"}
            size="sm"
          />
          <StatusChip
            status={runtimeReady ? "met" : "not_met"}
            label={runtimeReady ? "Runtime Ready" : "Runtime Blocked"}
            size="sm"
          />
          <StatusChip
            status="source"
            label={`${trialCatalog.totalCount} trials in scope`}
            size="sm"
          />
          {patientStageScopeLabel ? (
            <StatusChip
              status="source"
              label={patientStageScopeLabel}
              size="sm"
            />
          ) : null}
        </div>

        {!runtimeReady && patientReady && patientRuntimeReadiness?.reasonIfBenchmarkBlocked && populationMode !== "pharmacy_prescreen" && (
          <div className="pds-card" style={{ padding: 12, marginTop: 12 }}>
            <strong>Longitudinal lane blocked:</strong> {String(patientRuntimeReadiness.reasonIfBenchmarkBlocked).replace(/_/g, " ")}
          </div>
        )}

        {!patientReady && patientLoaded && (
          <div className="pds-card" style={{ padding: 12, marginTop: 12 }}>
            <strong>Linked longitudinal record required:</strong> patient-to-trials matching runs on one unified patient graph with a stable <code>Patient/...</code> reference.
          </div>
        )}

        {!runtimeReady && patientReady && patientRuntimeReadiness?.reasonIfOutreachBlocked && populationMode === "pharmacy_prescreen" && (
          <div className="pds-card" style={{ padding: 12, marginTop: 12 }}>
            <strong>Screening-enriched lane blocked:</strong> {String(patientRuntimeReadiness.reasonIfOutreachBlocked).replace(/_/g, " ")}
          </div>
        )}

        {matchError && (
          <div className="pds-card" style={{ padding: 12, marginTop: 12, color: "#dc2626" }}>
            {matchError}
          </div>
        )}
      </div>
    );
  };

  // ---- Right panel: Selected result detail (patient_to_trials) ----
  const renderRightP2T = () => {
    if (!selectedResult) {
      return (
        <div className="pds-card" style={{ padding: 24, textAlign: "center", color: "#64748b" }}>
          Select a trial from the results to see criterion-level detail
        </div>
      );
    }

    const trialId = selectedResult.trialId || selectedResult.nctId || "";
    const title = selectedResult.trialTitle || selectedResult.title || trialId;
    const decision = formatDecision(selectedResult.overall || selectedResult.decision);
    const allEvaluations = [
      ...(Array.isArray(selectedResult?.prefilter?.evaluations) ? selectedResult.prefilter.evaluations : []),
      ...(Array.isArray(selectedResult?.criteria) ? selectedResult.criteria : []),
    ];
    const resultGaps = Array.isArray(selectedResult?.gaps) ? selectedResult.gaps : [];

    return (
      <div>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>{title}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {trialId && <StatusChip status="source" label={trialId} size="sm" />}
            {selectedResult.phase && <StatusChip status="source" label={selectedResult.phase} size="sm" />}
            {selectedResult.status && <StatusChip status="source" label={selectedResult.status} size="sm" />}
            <StatusChip status={decision.status} label={decision.label} size="sm" />
          </div>
        </div>

        <CriterionStatusBar counts={selectedResultCounts} />

        <SectionDivider label="Criterion Trace" />

        {allEvaluations.length === 0 && (
          <p className="pds-muted" style={{ fontSize: 13 }}>No criterion evaluations available for this result.</p>
        )}

        {allEvaluations.map((ev, i) => (
          <CriterionCard
            key={ev.familyId || ev.ruleId || `crit-${i}`}
            status={ev.status}
            title={humanizeFamilyId(ev.familyId || ev.ruleId)}
            reason={ev.reason || ev.explanation || ""}
            familyId={ev.familyId}
            patientValue={ev.patientValue != null ? String(ev.patientValue) : undefined}
            trialValue={ev.trialValue != null ? String(ev.trialValue) : undefined}
            evidenceRefs={Array.isArray(ev.evidenceRefs) ? ev.evidenceRefs : undefined}
            evaluator={ev.evaluator}
            rawText={ev.rawText || ev.text}
          />
        ))}

        {resultGaps.length > 0 && (
          <>
            <SectionDivider label="Gaps" />
            {resultGaps.map((gap, i) => {
              const feature = typeof gap === "string" ? gap : (gap.feature || gap.name || "Unknown");
              const fillable = typeof gap === "object" && (gap.fillable || String(gap.fillableBy || "").includes("questionnaire"));
              const gapStatus = fillable ? "fillable" : "unknown";
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, fontSize: 13 }}>
                  <StatusChip status={gapStatus} size="sm" />
                  <span style={{ flex: 1 }}>{feature}</span>
                  {fillable && (
                    <span className="pds-muted" style={{ fontSize: 12 }}>
                      Questionnaire workflow planned
                    </span>
                  )}
                </div>
              );
            })}
          </>
        )}

        {trialId && (
          <div style={{ marginTop: 16 }}>
            <button
              type="button"
              className="pds-btn pds-btn--ghost"
              onClick={() => setShowCanonicalTree((prev) => !prev)}
            >
              {showCanonicalTree ? "Hide" : "View"} Canonical Tree
            </button>
            {showCanonicalTree && (
              <div style={{ marginTop: 8 }}>
                {treeLoading && <p className="pds-muted">Loading canonical tree...</p>}
                {!treeLoading && treePayload && (
                  <CriterionTreeView
                    treePayload={treePayload}
                    runtimeResult={selectedResult}
                    activeTab="runtime"
                  />
                )}
                {!treeLoading && !treePayload && (
                  <p className="pds-muted" style={{ fontSize: 13 }}>No canonical tree available for this trial.</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  /* =========================================================================
     Trial-to-Patients direction
     ========================================================================= */

  // ---- Left panel: Trial selector (trial_to_patients) ----
  const renderTrialSelector = () => (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
        <div className="pds-field" style={{ flex: "1 1 100px" }}>
          <input
            className="pds-field__input"
            type="text"
            placeholder="Search published trials..."
            value={catalogFilters.q}
            onChange={(e) => setCatalogFilters((prev) => ({ ...prev, q: e.target.value }))}
          />
        </div>
        <div className="pds-field" style={{ flex: "0 1 260px", minWidth: 220 }}>
          <select
            className="pds-field__input"
            value={catalogStageTargetWindow || ""}
            onChange={(e) => setCatalogStageTargetWindow(e.target.value)}
          >
            {TRIAL_CATALOG_SCOPE_OPTIONS.map((option) => (
              <option key={option.value || "all"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="pds-btn pds-btn--secondary"
          onClick={loadCatalog}
          disabled={catalogLoading}
          style={{ fontSize: 12 }}
        >
          Apply
        </button>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 4, marginBottom: 10 }}>
        <span className="pds-muted" style={{ fontSize: 11 }}>Published canonical contracts only.</span>
        <button
          type="button"
          className="pds-btn pds-btn--ghost"
          style={{ fontSize: 11, padding: "2px 8px" }}
          onClick={() => navigateSpace && navigateSpace("ingestion", { launchSource: "matching" })}
        >
          Trial Ingestion →
        </button>
      </div>

      <div style={{ maxHeight: 320, overflowY: "auto", marginBottom: 12 }}>
        {catalogLoading && <p className="pds-muted" style={{ fontSize: 13 }}>Loading...</p>}
        {!catalogLoading && catalogError && (
          <div className="pds-card" style={{ padding: 12, marginBottom: 8, color: "#475569" }}>
            <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginBottom: 4 }}>
              Trial catalog unavailable
            </div>
            <div style={{ fontSize: 12, lineHeight: 1.6 }}>
              {catalogError}
            </div>
          </div>
        )}
        {!catalogLoading && !catalogError && trialCatalog.trials.length === 0 && (
          <div className="pds-card" style={{ padding: 12, marginBottom: 8, color: "#475569" }}>
            <div style={{ fontWeight: 700, fontSize: 13, color: "#0f172a", marginBottom: 4 }}>
              No published trial contracts are loaded yet
            </div>
            <div style={{ fontSize: 12, lineHeight: 1.6, marginBottom: 10 }}>
              Open Clinical Trial Ingestion to search ClinicalTrials.gov, ingest one trial, and then come back here to run matching.
            </div>
            <button
              type="button"
              className="pds-btn pds-btn--secondary"
              onClick={() => navigateSpace && navigateSpace("ingestion", { launchSource: "matching" })}
            >
              Open Trial Ingestion &rarr;
            </button>
          </div>
        )}
        {trialCatalog.trials.map((trial, i) => {
          const trialId = getTrialIdentifier(trial) || `t-${i}`;
          const isSelected = getTrialIdentifier(selectedTrial) === trialId;
          const readiness = trial.runtimeReadiness || trial.readiness || {};
          const isRunnable = Boolean(readiness?.runnable || readiness?.runtimeReady);
          const trialStageTargetWindow = trial?.stage_relevance?.targetWindow || trial?.stageRelevance?.targetWindow || null;
          return (
            <ObjectCard
              key={trialId}
              avatar={trialId.slice(0, 3).toUpperCase()}
              title={trial.title || trial.brief_title || trial.briefTitle || trialId}
              subtitle={trialId}
              chips={[
                { label: isRunnable ? "Ready" : "Pending", status: isRunnable ? "met" : "unknown" },
                ...(trialStageTargetWindow
                  ? [{ label: humanizeStageTargetWindow(trialStageTargetWindow), status: "source" }]
                  : []),
              ]}
              selected={isSelected}
              onClick={() => loadTrialDetail(trialId)}
            />
          );
        })}
      </div>

      {selectedTrial && (
        <div>
          <ObjectCard
            avatar={getTrialIdentifier(selectedTrial).slice(0, 3).toUpperCase()}
            title={selectedTrial.title || selectedTrial.brief_title || selectedTrial.briefTitle || "Selected Trial"}
            subtitle={[selectedTrial.nct_id || selectedTrial.nctId, selectedTrial.phase, selectedTrial.status].filter(Boolean).join(" - ")}
            chips={selectedTrialStageTargetWindow ? [{ label: humanizeStageTargetWindow(selectedTrialStageTargetWindow), status: "source" }] : []}
            selected
          />

          <div style={{ marginTop: 8 }}>
            <ReadinessScorecard metrics={trialReadinessMetrics} />
          </div>

          <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className="pds-btn pds-btn--primary"
              onClick={() => openTrialWorkspace("overview")}
            >
              Open Trial Workspace
            </button>
            <button
              type="button"
              className="pds-btn pds-btn--secondary"
              disabled={matching || !selectedTrialRuntimeReadiness?.runnable || !matchingServiceReady}
              onClick={() => {
                openTrialWorkspace("screening");
                runTrialToPatients();
              }}
            >
              {matching ? "Screening..." : "Find Clinical Candidates"}
            </button>
            <button
              type="button"
              className="pds-btn pds-btn--ghost"
              onClick={() => navigateSpace && navigateSpace("ingestion", {
                launchSource: "matching",
                trialId: getTrialIdentifier(selectedTrial),
              })}
            >
              Open Clinical Trial Ingestion &rarr;
            </button>
          </div>
        </div>
      )}
    </div>
  );

  // ---- Center panel: Results (trial_to_patients) ----
  const renderCenterT2P = () => {
    const trialId = getTrialIdentifier(selectedTrial) || selectedTrialId;

    if (matching) {
      return (
        <div>
          <AgenticProgressTicker
            active
            title="Finding clinical candidates"
            subtitle={`Registry: ${populationRegistryLabel(populationMode)}`}
            steps={MATCH_STEPS_T2P}
          />
        </div>
      );
    }

    if (matchPayload && !matching) {
      return (
        <div style={{ display: "grid", gap: 16 }}>
          {renderTrialToPatientsStageBoard()}

          <div className="pharma-viability-canvas">
            <div className="pharma-viability-canvas__header">
              <div>
                <div className="pharma-viability-canvas__title">Clinical Candidate Screen</div>
                <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                  Deterministic eligibility narrows the registry to the patients who are still clinically relevant for this protocol.
                </div>
              </div>
              <StatusChip status="candidate" label={`${clinicallyRelevantCount} clinically relevant`} size="sm" />
            </div>
            <FunnelVisualization stages={clinicalFunnelStages} breakdown={clinicalFunnelBreakdown} />
            <div style={{ marginTop: 12 }}>
              <CriterionStatusBar counts={aggregateCounts} />
            </div>
          </div>

          {pharmaFilterApplied ? (
            <div className="pharma-viability-canvas accent">
              <div className="pharma-viability-canvas__header">
                <div>
                  <div className="pharma-viability-canvas__title">Pharmacy Viability Filter</div>
                  <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    This second pass turns the clinical pool into a shortlist that is easier to discuss with pharmacy and sponsors.
                  </div>
                </div>
                <StatusChip status="manual_review_required" label="Pharmacy lens applied" size="sm" />
              </div>
              <FunnelVisualization stages={pharmaFunnelStages} breakdown={pharmaFunnelBreakdown} />
              {pharmaActionPlan.length > 0 ? (
                <div className="pharma-viability-plan">
                  <div className="pharma-viability-plan__title">Pharmacy Action Plan</div>
                  <div className="pharma-viability-list">
                    {pharmaActionPlan.map((item) => (
                      <div key={item.title} className="pharma-viability-list__item">
                        <span className="pharma-viability-list__dot" />
                        <span><strong>{item.title}:</strong> {item.detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          {matchError && (
            <div className="pds-card" style={{ padding: 12, marginBottom: 12, color: "#dc2626" }}>
              Match error: {matchError}
            </div>
          )}

          <div className="pharma-viability-results-shell">
            <div className="pharma-viability-results-shell__header">
              <div>
                <div className="pharma-viability-results-shell__title">
                  {trialToPatientsLens === "pharmacy" && pharmaFilterApplied ? "Pharmacy Prioritization" : "Clinical Candidate Results"}
                </div>
                <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                  {trialToPatientsLens === "pharmacy" && pharmaFilterApplied
                    ? "Use these lanes to drive sponsor conversation, pharmacist review, and patient pre-screen planning."
                    : "Start with the deterministic clinical outcome before adding the pharmacy viability pass."}
                </div>
              </div>
              {pharmaFilterApplied ? (
                <StatusChip
                  status={trialToPatientsLens === "pharmacy" ? "manual_review_required" : "candidate"}
                  label={trialToPatientsLens === "pharmacy" ? "Pharmacy view" : "Clinical view"}
                  size="sm"
                />
              ) : null}
            </div>
            {renderTrialToPatientsResultsList()}
          </div>

          <div style={{ marginTop: 4, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className="pds-btn pds-btn--secondary"
              onClick={runTrialToPatients}
              disabled={!trialId}
            >
              Re-run Clinical Screening
            </button>
            {pharmaFilterApplied ? (
              <button
                type="button"
                className="pds-btn pds-btn--ghost"
                onClick={() => setTrialToPatientsLens((prev) => (prev === "clinical" ? "pharmacy" : "clinical"))}
              >
                Switch to {trialToPatientsLens === "clinical" ? "Pharmacy" : "Clinical"} View
              </button>
            ) : null}
          </div>
        </div>
      );
    }

    // Pre-run
    return (
      <div>
        <div
          style={{
            padding: 14,
            borderRadius: 12,
            border: "1px solid var(--pharma-border, #e2e8f0)",
            background: "var(--pharma-bg, #f8fafc)",
            marginBottom: 16,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--pharma-text, #0f172a)", marginBottom: 6 }}>
            Current run
          </div>
          <div className="pds-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
            {trialId
              ? `This run will first screen the unified patient registry against the selected canonical trial contract using the ${populationLaneLabel(populationMode)}, then optionally apply a pharmacy viability filter to the resulting candidate pool.`
              : "Choose one published trial first, then screen the current patient registry against its canonical contract."}
          </div>
          {catalogStageScopeLabel ? (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>
              Trial catalog filter: {catalogStageScopeLabel}.
            </div>
          ) : (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>
              Trial catalog filter: All runnable breast-cancer contracts.
            </div>
          )}
          {selectedTrialStageTargetWindow ? (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>
              Trial window: {humanizeStageTargetWindow(selectedTrialStageTargetWindow)}.
            </div>
          ) : null}
        </div>

        <button
          type="button"
          className="pds-btn pds-btn--primary"
          style={{ width: "100%", marginBottom: 16, padding: "12px 24px", fontSize: 15 }}
          disabled={!trialId || matching || !matchingServiceReady}
          onClick={runTrialToPatients}
        >
          Find Clinical Candidates
        </button>

        {serviceHealthy !== true ? (
          <div className="pds-muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 16 }}>
            {serviceHealthy === null ? "Checking clinical trials sidecar readiness..." : MATCHING_SERVICE_UNAVAILABLE_COPY}
          </div>
        ) : null}

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <StatusChip
            status={trialId ? "met" : "unknown"}
            label={trialId ? "Trial Selected" : "No Trial Selected"}
            size="sm"
          />
          <StatusChip
            status="source"
            label={populationRegistryLabel(populationMode)}
            size="sm"
          />
          <StatusChip
            status={catalogStageScopeLabel ? "source" : "neutral"}
            label={catalogStageScopeLabel || "All Runnable Breast Trials"}
            size="sm"
          />
          {selectedTrialStageTargetWindow ? (
            <StatusChip
              status="source"
              label={humanizeStageTargetWindow(selectedTrialStageTargetWindow)}
              size="sm"
            />
          ) : null}
        </div>

        {matchError && (
          <div className="pds-card" style={{ padding: 12, marginTop: 12, color: "#dc2626" }}>
            {matchError}
          </div>
        )}
      </div>
    );
  };

  // ---- Right panel: Selected candidate detail (trial_to_patients) ----
  const renderRightT2P = () => {
    if (!selectedResult) {
      return (
        <div className="pds-card" style={{ padding: 24, textAlign: "center", color: "#64748b" }}>
          Select a patient from the results to see the clinical trace and pharmacy viability summary
        </div>
      );
    }

    const pName = selectedResult.patientName || selectedResult.name || selectedResult.patientRef || "Patient";
    const pAge = selectedResult.age;
    const pSex = selectedResult.sex || selectedResult.gender;
    const decision = formatDecision(selectedResult.overall || selectedResult.decision);
    const allEvaluations = [
      ...(Array.isArray(selectedResult?.prefilter?.evaluations) ? selectedResult.prefilter.evaluations : []),
      ...(Array.isArray(selectedResult?.criteria) ? selectedResult.criteria : []),
    ];
    const resultGaps = Array.isArray(selectedResult?.gaps) ? selectedResult.gaps : [];

    return (
      <div>
        <ObjectCard
          avatar={pName.slice(0, 2).toUpperCase()}
          title={pName}
          subtitle={[pAge && `${pAge}y`, pSex].filter(Boolean).join(" / ")}
          chips={[
            { label: `Clinical: ${decision.label}`, status: decision.status },
            ...(selectedResultPharmaReview
              ? [{ label: `Pharma: ${selectedResultPharmaReview.shortLabel}`, status: selectedResultPharmaReview.status }]
              : []),
          ]}
          selected
        />

        <div style={{ marginTop: 8 }}>
          <CriterionStatusBar counts={selectedResultCounts} />
        </div>

        <SectionDivider label="Pharmacy Viability" />

        {renderPharmaSummaryCard(selectedResultPharmaReview)}

        <SectionDivider label="Clinical Trace" />

        {allEvaluations.length === 0 && (
          <p className="pds-muted" style={{ fontSize: 13 }}>No criterion evaluations available for this result.</p>
        )}

        {allEvaluations.map((ev, i) => (
          <CriterionCard
            key={ev.familyId || ev.ruleId || `crit-${i}`}
            status={ev.status}
            title={humanizeFamilyId(ev.familyId || ev.ruleId)}
            reason={ev.reason || ev.explanation || ""}
            familyId={ev.familyId}
            patientValue={ev.patientValue != null ? String(ev.patientValue) : undefined}
            trialValue={ev.trialValue != null ? String(ev.trialValue) : undefined}
            evidenceRefs={Array.isArray(ev.evidenceRefs) ? ev.evidenceRefs : undefined}
            evaluator={ev.evaluator}
            rawText={ev.rawText || ev.text}
          />
        ))}

        {resultGaps.length > 0 && (
          <>
            <SectionDivider label="Gaps" />
            {resultGaps.map((gap, i) => {
              const feature = typeof gap === "string" ? gap : (gap.feature || gap.name || "Unknown");
              const fillable = typeof gap === "object" && (gap.fillable || String(gap.fillableBy || "").includes("questionnaire"));
              const gapStatus = fillable ? "fillable" : "unknown";
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, fontSize: 13 }}>
                  <StatusChip status={gapStatus} size="sm" />
                  <span style={{ flex: 1 }}>{feature}</span>
                  {fillable && (
                    <span className="pds-muted" style={{ fontSize: 12 }}>
                      Questionnaire workflow planned
                    </span>
                  )}
                </div>
              );
            })}
          </>
        )}

        <div style={{ marginTop: 12 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button
              type="button"
              className="pds-btn pds-btn--secondary"
              onClick={() => openTrialWorkspace("evidence")}
            >
              Open Evidence & Trace
            </button>
            <button
              type="button"
              className="pds-btn pds-btn--ghost"
              onClick={() => navigateSpace && navigateSpace("patient", {
                launchSource: "matching",
                patientRef: selectedResult?.patientRef || "",
              })}
            >
              Open Patient Data &rarr;
            </button>
          </div>
        </div>
      </div>
    );
  };

  const renderTrialWorkspace = () => {
    if (direction !== "trial_to_patients" || !selectedTrial || !trialWorkspaceOpen) return null;

    const trialId = getTrialIdentifier(selectedTrial);
    const trialTitle = selectedTrial.title || selectedTrial.brief_title || selectedTrial.briefTitle || trialId || "Selected Trial";
    const overviewMetrics = [
      {
        label: "Coverage",
        value: selectedTrialRuntimeReadiness?.coveragePct != null ? `${selectedTrialRuntimeReadiness.coveragePct}%` : "-",
        tone: selectedTrialRuntimeReadiness?.runnable ? "positive" : "neutral",
      },
      {
        label: "Deterministic",
        value: selectedTrialRuntimeReadiness?.deterministicPatientImpactingCount ?? selectedTrial?.criteria_summary?.deterministic_rules ?? "-",
        tone: "positive",
      },
      {
        label: "Manual",
        value: selectedTrialRuntimeReadiness?.manualPatientImpactingCount ?? selectedTrial?.criteria_summary?.manual_review_rules ?? "-",
        tone: "neutral",
      },
      {
        label: "Unsupported",
        value: selectedTrialRuntimeReadiness?.unsupportedPatientImpactingCount ?? selectedTrial?.criteria_summary?.unsupported_rules ?? "-",
        tone: selectedTrialRuntimeReadiness?.unsupportedPatientImpactingCount > 0 ? "caution" : "positive",
      },
    ];

    return (
      <div
        style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(15,23,42,0.35)", display: "flex", justifyContent: "flex-end" }}
        onClick={(e) => { if (e.target === e.currentTarget) setTrialWorkspaceOpen(false); }}
      >
        <div
          ref={trialWorkspaceRef}
          style={{
            width: "min(920px, 92vw)", height: "100%", background: "#fff",
            boxShadow: "-4px 0 32px rgba(15,23,42,0.14)",
            display: "flex", flexDirection: "column", overflow: "hidden",
          }}
        >
          {/* Sticky header */}
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "10px 20px", borderBottom: "1px solid #e2e8f0",
            background: "#fff", flexShrink: 0,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
              <div>
                <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "#94a3b8" }}>Trial Workspace</div>
                <div style={{ fontSize: 14, fontWeight: 600, color: "#0f172a", marginTop: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "60vw" }}>
                  {trialTitle}
                </div>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {trialId ? <StatusChip status="source" label={trialId} size="sm" /> : null}
                <StatusChip status={selectedTrialRuntimeReadiness?.runnable ? "met" : "not_met"} label={selectedTrialRuntimeReadiness?.runnable ? "Ready" : "Blocked"} size="sm" />
                {selectedTrial.phase ? <StatusChip status="source" label={formatPhaseLabel(selectedTrial.phase)} size="sm" /> : null}
                {selectedTrialSponsorLabel ? <StatusChip status="neutral" label={selectedTrialSponsorLabel} size="sm" /> : null}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
              {selectedTrialSourceUrl ? (
                <a href={selectedTrialSourceUrl} target="_blank" rel="noreferrer" className="pds-btn pds-btn--ghost" style={{ fontSize: 12, padding: "4px 10px" }}>
                  Source
                </a>
              ) : null}
              <button
                type="button"
                className="pds-btn pds-btn--secondary"
                style={{ fontSize: 12, padding: "4px 10px" }}
                disabled={matching || !selectedTrialRuntimeReadiness?.runnable || !matchingServiceReady}
                onClick={() => { setTrialWorkspaceTab("screening"); runTrialToPatients(); }}
              >
                {matching ? "Screening..." : "Find Clinical Candidates"}
              </button>
              <button
                type="button"
                onClick={() => setTrialWorkspaceOpen(false)}
                style={{ background: "none", border: "none", cursor: "pointer", color: "#94a3b8", fontSize: 20, lineHeight: 1, padding: "4px 8px" }}
                title="Close"
              >
                ✕
              </button>
            </div>
          </div>

          {/* Tab bar */}
          <div style={{ display: "flex", gap: 0, borderBottom: "1px solid #e2e8f0", background: "#fff", flexShrink: 0 }}>
            {[
              ["overview", "Overview"],
              ["contract", "Canonical Contract"],
              ["screening", "Clinical Screening"],
              ["evidence", "Evidence & Trace"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTrialWorkspaceTab(id)}
                style={{
                  fontSize: 12, padding: "8px 16px", border: "none", background: "none", cursor: "pointer",
                  fontWeight: trialWorkspaceTab === id ? 600 : 400,
                  color: trialWorkspaceTab === id ? "#0f172a" : "#64748b",
                  borderBottom: trialWorkspaceTab === id ? "2px solid #0f172a" : "2px solid transparent",
                  transition: "color 0.1s",
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Scrollable body */}
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>

            {trialWorkspaceTab === "overview" ? (
              <div style={{ display: "grid", gap: 16 }}>
                <ReadinessScorecard metrics={overviewMetrics} />
                <DeterministicEvidencePanel row={selectedTrial} title="How this contract is prepared for deterministic matching" />

                <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(0, 0.8fr)", gap: 16 }}>
                  <div className="pds-card" style={{ padding: 16 }}>
                    <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 10 }}>Study Summary</div>
                    <div className="pds-muted" style={{ fontSize: 13, lineHeight: 1.75, color: "var(--pharma-text-secondary, #475569)" }}>
                      {selectedTrialSummary || "No summary available for this trial."}
                    </div>
                    {selectedTrialRuntimeReadiness?.reasonIfBlocked ? (
                      <div className="pds-card" style={{ padding: 12, marginTop: 14 }}>
                        <strong>Readiness note:</strong> {selectedTrialRuntimeReadiness.reasonIfBlocked}
                      </div>
                    ) : null}
                  </div>

                  <div className="pds-card" style={{ padding: 16 }}>
                    <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 10 }}>Contract Facts</div>
                    <div className="pharma-kv-grid">
                      <div><span>Compile status</span><strong>{selectedTrial?.compile?.status || "-"}</strong></div>
                      <div><span>Version</span><strong>{selectedTrial?.current_version_id || "-"}</strong></div>
                      <div><span>Criteria nodes</span><strong>{selectedTrial?.criteria_summary?.total_nodes ?? selectedTrial?.criteriaSummary?.totalNodes ?? "-"}</strong></div>
                      <div><span>Review pending</span><strong>{selectedTrial?.review_snapshot?.pending_count ?? selectedTrial?.compile?.review_pending_count ?? 0}</strong></div>
                      <div><span>Sites</span><strong>{selectedTrialSites.length}</strong></div>
                      <div><span>Match mode</span><strong>{formatRuntimeMode(selectedTrialRuntimeReadiness?.mode || "canonical_only")}</strong></div>
                    </div>
                  </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16 }}>
                  <div className="pds-card" style={{ padding: 16 }}>
                    <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 10 }}>
                      Inclusion Criteria ({selectedTrialCriteria.inclusion.length})
                    </div>
                    <div style={{ maxHeight: 320, overflowY: "auto", display: "grid", gap: 10 }}>
                      {selectedTrialCriteria.inclusion.length > 0 ? selectedTrialCriteria.inclusion.map((item, index) => (
                        <div key={`incl-${index}`} style={{ display: "grid", gridTemplateColumns: "24px 1fr", gap: 8, alignItems: "start" }}>
                          <span className="pharma-evidence-chip pass" style={{ justifyContent: "center", fontSize: 10 }}>{index + 1}</span>
                          <div style={{ fontSize: 12, lineHeight: 1.65, color: "var(--pharma-text, #0f172a)" }}>{item}</div>
                        </div>
                      )) : <div className="pds-muted" style={{ fontSize: 12 }}>No explicit inclusion criteria parsed.</div>}
                    </div>
                  </div>

                  <div className="pds-card" style={{ padding: 16 }}>
                    <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 10 }}>
                      Exclusion Criteria ({selectedTrialCriteria.exclusion.length})
                    </div>
                    <div style={{ maxHeight: 320, overflowY: "auto", display: "grid", gap: 10 }}>
                      {selectedTrialCriteria.exclusion.length > 0 ? selectedTrialCriteria.exclusion.map((item, index) => (
                        <div key={`excl-${index}`} style={{ display: "grid", gridTemplateColumns: "24px 1fr", gap: 8, alignItems: "start" }}>
                          <span className="pharma-evidence-chip blocked" style={{ justifyContent: "center", fontSize: 10 }}>{index + 1}</span>
                          <div style={{ fontSize: 12, lineHeight: 1.65, color: "var(--pharma-text, #0f172a)" }}>{item}</div>
                        </div>
                      )) : <div className="pds-muted" style={{ fontSize: 12 }}>No explicit exclusion criteria parsed.</div>}
                    </div>
                  </div>
                </div>

                {selectedTrialSites.length > 0 ? (
                  <div className="pds-card" style={{ padding: 16 }}>
                    <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 10 }}>
                      Site Coverage ({selectedTrialSites.length} sites)
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 8, maxHeight: 240, overflowY: "auto" }}>
                      {selectedTrialSites.map((site, index) => {
                        const city = site?.city || site?.facility?.city || site?.location_city || "";
                        const state = site?.state || site?.facility?.state || site?.location_state || "";
                        const country = site?.country || site?.facility?.country || site?.location_country || "";
                        const label = [city, state, country].filter(Boolean).join(", ");
                        return (
                          <div key={`site-${index}`} style={{ padding: "8px 10px", background: "#f8fafc", borderRadius: 6, border: "1px solid #e2e8f0" }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: "#0f172a" }}>
                              {site?.name || site?.facility?.name || "Research Site"}
                            </div>
                            <div className="pds-muted" style={{ fontSize: 11, marginTop: 2 }}>
                              {label || "Location unavailable"}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {trialWorkspaceTab === "contract" ? (
              treePayload ? (
                <ClinicalTrialCanonicalWorkspace
                  trial={selectedTrial}
                  treePayload={treePayload}
                  runtimeResult={selectedResult}
                  runtimeLabel={selectedResult?.patientName || selectedResult?.patientRef || ""}
                />
              ) : (
                <div className="pds-card" style={{ padding: 16 }}>
                  {treeLoading ? "Loading canonical tree..." : "No canonical tree available for this trial yet."}
                </div>
              )
            ) : null}

            {trialWorkspaceTab === "screening" ? (
              <div style={{ display: "grid", gap: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <div className="pds-muted" style={{ fontSize: 12 }}>
                    Screen the current unified patient registry against this canonical contract, then optionally apply the pharmacy viability lens in the main canvas.
                  </div>
                  <button
                    type="button"
                    className="pds-btn pds-btn--primary"
                    style={{ fontSize: 12, padding: "5px 12px" }}
                    disabled={matching || !selectedTrialRuntimeReadiness?.runnable || !matchingServiceReady}
                    onClick={runTrialToPatients}
                  >
                    {matching ? "Running..." : "Find Clinical Candidates"}
                  </button>
                </div>

                {matching ? <AgenticProgressTicker active title="Finding clinical candidates" subtitle={`Registry: ${populationRegistryLabel(populationMode)}`} steps={MATCH_STEPS_T2P} /> : null}

                {matchPayload ? (
                  <>
                    <FunnelVisualization stages={clinicalFunnelStages} breakdown={clinicalFunnelBreakdown} />
                    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                      <div className="pds-card" style={{ padding: "10px 14px", flex: "1 1 120px" }}>
                        <div className="pds-muted" style={{ fontSize: 11 }}>Cohort</div>
                        <strong style={{ display: "block", fontSize: 22, marginTop: 4 }}>{matchPayload?.catalogCount ?? matchPayload?.evaluatedCount ?? 0}</strong>
                      </div>
                      <div className="pds-card" style={{ padding: "10px 14px", flex: "1 1 120px" }}>
                        <div className="pds-muted" style={{ fontSize: 11 }}>Returned</div>
                        <strong style={{ display: "block", fontSize: 22, marginTop: 4 }}>{matchResults.length}</strong>
                      </div>
                      <div className="pds-card" style={{ padding: "10px 14px", flex: "1 1 120px" }}>
                        <div className="pds-muted" style={{ fontSize: 11 }}>Run mode</div>
                        <strong style={{ display: "block", fontSize: 13, marginTop: 4 }}>{formatRuntimeMode(matchPayload?.runtimeMode)}</strong>
                      </div>
                    </div>

                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 0.9fr) minmax(0, 1.1fr)", gap: 16 }}>
                      <div className="pds-card" style={{ padding: 14 }}>
                        <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 8, fontSize: 11 }}>Clinical Candidate Results</div>
                        <div style={{ maxHeight: 600, overflowY: "auto", display: "grid", gap: 8 }}>
                          {groupedResults.candidate.map((row, index) => renderResultCard(row, index))}
                          {groupedResults.needs_data.map((row, index) => renderResultCard(row, groupedResults.candidate.length + index))}
                          {groupedResults.manual.map((row, index) => renderResultCard(row, groupedResults.candidate.length + groupedResults.needs_data.length + index))}
                          {showExcluded ? groupedResults.excluded.map((row, index) => renderResultCard(row, groupedResults.candidate.length + groupedResults.needs_data.length + groupedResults.manual.length + index)) : null}
                        </div>
                        {groupedResults.excluded.length > 0 ? (
                          <button type="button" className="pds-btn pds-btn--ghost" style={{ marginTop: 10, fontSize: 12 }} onClick={() => setShowExcluded((prev) => !prev)}>
                            {showExcluded ? "Hide" : "Show"} {groupedResults.excluded.length} excluded patients
                          </button>
                        ) : null}
                      </div>

                      <div className="pds-card" style={{ padding: 14 }}>
                        <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 8, fontSize: 11 }}>Candidate Review</div>
                        {selectedResult ? (
                          <>
                            <CriterionStatusBar counts={selectedResultCounts} />
                            <div style={{ marginTop: 12 }}>
                              {renderPharmaSummaryCard(selectedResult?.pharmaReview || buildPharmaViability(selectedResult), { compact: true })}
                            </div>
                          </>
                        ) : (
                          <div className="pds-muted" style={{ fontSize: 12 }}>Select a patient from the results to inspect their eligibility story.</div>
                        )}
                      </div>
                    </div>
                  </>
                ) : !matching ? (
                  <div className="pds-muted" style={{ fontSize: 12, padding: "12px 0" }}>Run candidate screening to populate results.</div>
                ) : null}
              </div>
            ) : null}

            {trialWorkspaceTab === "evidence" ? (
              <div style={{ display: "grid", gap: 16 }}>
                {selectedResult ? (
                  <>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap", padding: "4px 0", borderBottom: "1px solid #e2e8f0" }}>
                      <div className="pds-muted" style={{ fontSize: 12 }}>
                        Criterion-by-criterion evidence for {selectedResult?.patientName || selectedResult?.patientRef || "selected patient"}
                      </div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <StatusChip status={formatDecision(selectedResult?.overall || selectedResult?.decision).status} label={formatDecision(selectedResult?.overall || selectedResult?.decision).label} size="sm" />
                        {selectedResult?.patientRef ? <StatusChip status="neutral" label={selectedResult.patientRef} size="sm" /> : null}
                      </div>
                    </div>

                    {selectedResult?.evidencePack ? (
                      <div className="pds-card" style={{ padding: 14 }}>
                        <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 8, fontSize: 11 }}>Patient Evidence Pack</div>
                        <EvidencePackSummary evidencePack={selectedResult.evidencePack} />
                      </div>
                    ) : null}

                    <div className="pds-card" style={{ padding: 14 }}>
                      <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 8, fontSize: 11 }}>Decision Summary</div>
                      <CandidateEvaluationPanel row={selectedResult} expanded />
                    </div>

                    {Array.isArray(selectedResult?.ruleEvaluations) && selectedResult.ruleEvaluations.length > 0 ? (
                      <div className="pds-card" style={{ padding: 14 }}>
                        <div className="pds-field__label" style={{ fontWeight: 700, marginBottom: 8, fontSize: 11 }}>Rule-by-Rule Evidence</div>
                        <RuleEvaluationTable rules={selectedResult.ruleEvaluations} maxRows={24} />
                      </div>
                    ) : null}

                    {treePayload ? (
                      <ClinicalTrialCanonicalWorkspace
                        trial={selectedTrial}
                        treePayload={treePayload}
                        runtimeResult={selectedResult}
                        runtimeLabel={selectedResult?.patientName || selectedResult?.patientRef || ""}
                      />
                    ) : null}
                  </>
                ) : (
                  <div className="pds-muted" style={{ fontSize: 12, padding: "12px 0" }}>
                    Run candidate screening and select a patient to inspect the full evidence-and-trace view.
                  </div>
                )}
              </div>
            ) : null}

          </div>
        </div>
      </div>
    );
  };

  /* =========================================================================
     Canvas layout helpers (Option-C redesign)
     ========================================================================= */

  const renderRailContent = () => {
    if (matching) {
      const pct = matchProgress?.total > 0
        ? Math.round((matchProgress.current / matchProgress.total) * 100)
        : matchProgress?.step && matchProgress?.steps
          ? Math.round(((matchProgress.step - 1) / matchProgress.steps) * 100)
          : null;
      const overallPct = matchProgress?.step && matchProgress?.steps
        ? Math.round((matchProgress.step / matchProgress.steps) * 100)
        : pct ?? 0;

      return (
        <div style={{ padding: "4px 0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span className="pharma-agentic-spinner" aria-hidden="true" />
            <span style={{ fontSize: 12, fontWeight: 600, color: "#0f172a" }}>
              {direction === "patient_to_trials" ? "Running canonical match" : "Finding clinical candidates"}
            </span>
          </div>

          {matchProgress ? (
            <>
              <div style={{ fontSize: 12, color: "#475569", marginBottom: 6, lineHeight: 1.5 }}>
                {matchProgress.label}
              </div>

              <div style={{ height: 6, borderRadius: 3, background: "#e2e8f0", overflow: "hidden", marginBottom: 4 }}>
                <div
                  style={{
                    height: "100%",
                    borderRadius: 3,
                    background: "linear-gradient(90deg, #00684a, #00ed64)",
                    width: `${overallPct}%`,
                    transition: "width 0.3s ease",
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#94a3b8", marginBottom: 10 }}>
                <span>Step {matchProgress.step} of {matchProgress.steps}</span>
                <span>{overallPct}%</span>
              </div>

              {matchProgress.total > 0 && (
                <>
                  <div style={{ height: 4, borderRadius: 2, background: "#f1f5f9", overflow: "hidden", marginBottom: 4 }}>
                    <div
                      style={{
                        height: "100%",
                        borderRadius: 2,
                        background: "#94a3b8",
                        width: `${Math.round((matchProgress.current / matchProgress.total) * 100)}%`,
                        transition: "width 0.15s ease",
                      }}
                    />
                  </div>
                  <div style={{ fontSize: 11, color: "#94a3b8" }}>
                    {matchProgress.current} / {matchProgress.total}
                  </div>
                </>
              )}
            </>
          ) : (
            <div style={{ fontSize: 12, color: "#94a3b8" }}>Initializing…</div>
          )}
        </div>
      );
    }

    if (matchPayload) {
      const rerunDisabled = direction === "patient_to_trials" ? !patientReady : !getTrialIdentifier(selectedTrial);
      return (
        <div>
          <div style={{ marginBottom: 10 }}>
            <CriterionStatusBar counts={aggregateCounts} />
          </div>
          {matchError && (
            <div className="pds-card" style={{ padding: 8, color: "#dc2626", marginBottom: 8, fontSize: 12 }}>
              {matchError}
            </div>
          )}
          {direction === "trial_to_patients"
            ? renderTrialToPatientsResultsList()
            : (
              <>
                {groupedResults.candidate.length > 0 && (
                  <>
                    <SectionDivider label={`Candidates (${groupedResults.candidate.length})`} />
                    {groupedResults.candidate.map((row, i) => renderResultCard(row, i))}
                  </>
                )}
                {groupedResults.needs_data.length > 0 && (
                  <>
                    <SectionDivider label={`Needs More Data (${groupedResults.needs_data.length})`} />
                    {groupedResults.needs_data.map((row, i) => renderResultCard(row, i))}
                  </>
                )}
                {groupedResults.manual.length > 0 && (
                  <>
                    <SectionDivider label={`Manual Review (${groupedResults.manual.length})`} />
                    {groupedResults.manual.map((row, i) => renderResultCard(row, i))}
                  </>
                )}
                {groupedResults.excluded.length > 0 && (
                  <>
                    <div style={{ marginTop: 10 }}>
                      <button
                        type="button"
                        className="pds-btn pds-btn--ghost"
                        style={{ fontSize: 12 }}
                        onClick={() => setShowExcluded((p) => !p)}
                      >
                        {showExcluded ? "Hide" : "Show"} {groupedResults.excluded.length} excluded
                      </button>
                    </div>
                    {showExcluded && groupedResults.excluded.map((row, i) => renderResultCard(row, i))}
                  </>
                )}
              </>
            )
          }
          <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px solid #e2e8f0" }}>
            <button
              type="button"
              className="pds-btn pds-btn--secondary"
              style={{ width: "100%", fontSize: 12 }}
              onClick={direction === "patient_to_trials" ? runPatientToTrials : runTrialToPatients}
              disabled={rerunDisabled}
            >
              Re-run
            </button>
          </div>
        </div>
      );
    }

    if (direction === "patient_to_trials") {
      return (
        <div>
          {patientLoading && <p className="pds-muted" style={{ fontSize: 13 }}>Loading patient...</p>}
          {patientError && !patientLoading && (
            <div style={{ color: "#dc2626", fontSize: 13, marginBottom: 8 }}>{patientError}</div>
          )}
          {!patientLoading && !patientContext && !patientError && renderPatientSelector()}
          {!patientLoading && patientContext && renderPatientLoaded()}
        </div>
      );
    }

    return renderTrialSelector();
  };

  const renderCanvasOverview = () => {
    const trialId = getTrialIdentifier(selectedTrial) || selectedTrialId;

    if (direction === "patient_to_trials") {
      if (matching) {
        return null; // progress shown in the left rail
      }
      if (matchPayload) {
        return (
          <div>
            <FunnelVisualization stages={defaultFunnelStages} />
            <div style={{ margin: "12px 0" }}>
              <CriterionStatusBar counts={aggregateCounts} />
            </div>
            {matchError && (
              <div className="pds-card" style={{ padding: 12, marginBottom: 12, color: "#dc2626" }}>
                Match error: {matchError}
              </div>
            )}
          </div>
        );
      }
      return (
        <div>
          <div style={{ padding: 14, borderRadius: 12, border: "1px solid var(--pharma-border, #e2e8f0)", background: "var(--pharma-bg, #f8fafc)", marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--pharma-text, #0f172a)", marginBottom: 6 }}>Current run</div>
            <div className="pds-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
              {patientReady
                ? `This run will screen ${trialCatalog.totalCount || 0} ${patientStageScopeLabel ? `${patientStageScopeLabel.toLowerCase()} breast trial contracts` : "runtime-ready canonical trials"} for the loaded patient using the ${populationLaneLabel(populationMode)} of the unified patient graph.`
                : patientLoaded
                  ? "This patient still needs a linked longitudinal record before patient-to-trials matching can run. Resolve identity and evidence in Patient Data first."
                  : "Load a patient from the left panel, then run a deterministic screen across the published canonical trial corpus."}
            </div>
            {patientStageScopeLabel && (
              <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>
                Mixed/manual stage contracts remain in scope so the demo still traverses every rule.
              </div>
            )}
          </div>
          <button
            type="button"
            className="pds-btn pds-btn--primary"
            style={{ width: "100%", marginBottom: 16, padding: "12px 24px", fontSize: 15 }}
            disabled={!patientReady || !runtimeReady || matching || !matchingServiceReady}
            onClick={runPatientToTrials}
          >
            Run Canonical Match
          </button>
          {serviceHealthy !== true && (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 16 }}>
              {serviceHealthy === null ? "Checking clinical trials sidecar readiness..." : MATCHING_SERVICE_UNAVAILABLE_COPY}
            </div>
          )}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <StatusChip status={patientReady ? "met" : patientLoaded ? "not_met" : "unknown"} label={patientReady ? "Patient Graph Loaded" : patientLoaded ? "Patient Needs Linkage" : "Patient Pending"} size="sm" />
            <StatusChip status={runtimeReady ? "met" : "not_met"} label={runtimeReady ? "Runtime Ready" : "Runtime Blocked"} size="sm" />
            <StatusChip status="source" label={`${trialCatalog.totalCount} trials in scope`} size="sm" />
            {patientStageScopeLabel && <StatusChip status="source" label={patientStageScopeLabel} size="sm" />}
          </div>
          {!runtimeReady && patientReady && patientRuntimeReadiness?.reasonIfBenchmarkBlocked && populationMode !== "pharmacy_prescreen" && (
            <div className="pds-card" style={{ padding: 12, marginTop: 12 }}>
              <strong>Longitudinal lane blocked:</strong> {String(patientRuntimeReadiness.reasonIfBenchmarkBlocked).replace(/_/g, " ")}
            </div>
          )}
          {matchError && (
            <div className="pds-card" style={{ padding: 12, marginTop: 12, color: "#dc2626" }}>{matchError}</div>
          )}
        </div>
      );
    }

    // trial_to_patients
    if (matching) {
      return null; // progress shown in the left rail
    }
    if (matchPayload) {
      return (
        <div style={{ display: "grid", gap: 16 }}>
          {renderTrialToPatientsStageBoard()}
          <div className="pharma-viability-canvas">
            <div className="pharma-viability-canvas__header">
              <div>
                <div className="pharma-viability-canvas__title">Clinical Candidate Screen</div>
                <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                  Deterministic eligibility narrows the registry to the patients who are still clinically relevant for this protocol.
                </div>
              </div>
              <StatusChip status="candidate" label={`${clinicallyRelevantCount} clinically relevant`} size="sm" />
            </div>
            <FunnelVisualization stages={clinicalFunnelStages} breakdown={clinicalFunnelBreakdown} />
            <div style={{ marginTop: 12 }}>
              <CriterionStatusBar counts={aggregateCounts} />
            </div>
          </div>
          {pharmaFilterApplied && (
            <div className="pharma-viability-canvas accent">
              <div className="pharma-viability-canvas__header">
                <div>
                  <div className="pharma-viability-canvas__title">Pharmacy Viability Filter</div>
                  <div className="pds-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    This second pass turns the clinical pool into a shortlist that is easier to discuss with pharmacy and sponsors.
                  </div>
                </div>
                <StatusChip status="manual_review_required" label="Pharmacy lens applied" size="sm" />
              </div>
              <FunnelVisualization stages={pharmaFunnelStages} breakdown={pharmaFunnelBreakdown} />
              {pharmaActionPlan.length > 0 && (
                <div className="pharma-viability-plan">
                  <div className="pharma-viability-plan__title">Pharmacy Action Plan</div>
                  <div className="pharma-viability-list">
                    {pharmaActionPlan.map((item) => (
                      <div key={item.title} className="pharma-viability-list__item">
                        <span className="pharma-viability-list__dot" />
                        <span><strong>{item.title}:</strong> {item.detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          {matchError && (
            <div className="pds-card" style={{ padding: 12, color: "#dc2626" }}>Match error: {matchError}</div>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="pds-btn pds-btn--secondary" onClick={runTrialToPatients} disabled={!trialId}>
              Re-run Clinical Screening
            </button>
            {!pharmaFilterApplied && (
              <button type="button" className="pds-btn pds-btn--ghost" onClick={applyPharmaFilter}>
                Apply Pharmacy Filter
              </button>
            )}
            {pharmaFilterApplied && (
              <button
                type="button"
                className="pds-btn pds-btn--ghost"
                onClick={() => setTrialToPatientsLens((p) => (p === "clinical" ? "pharmacy" : "clinical"))}
              >
                Switch to {trialToPatientsLens === "clinical" ? "Pharmacy" : "Clinical"} View
              </button>
            )}
          </div>
        </div>
      );
    }
    return (
      <div>
        <div style={{ padding: 14, borderRadius: 12, border: "1px solid var(--pharma-border, #e2e8f0)", background: "var(--pharma-bg, #f8fafc)", marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "var(--pharma-text, #0f172a)", marginBottom: 6 }}>Current run</div>
          <div className="pds-muted" style={{ fontSize: 13, lineHeight: 1.6 }}>
            {trialId
              ? `This run will first screen the unified patient registry against the selected canonical trial contract using the ${populationLaneLabel(populationMode)}, then optionally apply a pharmacy viability filter.`
              : "Choose one published trial from the left panel first, then screen the current patient registry against its canonical contract."}
          </div>
          {catalogStageScopeLabel ? (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>Trial catalog filter: {catalogStageScopeLabel}.</div>
          ) : (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>Trial catalog filter: All runnable breast-cancer contracts.</div>
          )}
          {selectedTrialStageTargetWindow && (
            <div className="pds-muted" style={{ fontSize: 12, marginTop: 8 }}>
              Trial window: {humanizeStageTargetWindow(selectedTrialStageTargetWindow)}.
            </div>
          )}
        </div>
        <button
          type="button"
          className="pds-btn pds-btn--primary"
          style={{ width: "100%", marginBottom: 16, padding: "12px 24px", fontSize: 15 }}
          disabled={!trialId || matching || !matchingServiceReady}
          onClick={runTrialToPatients}
        >
          Find Clinical Candidates
        </button>
        {serviceHealthy !== true && (
          <div className="pds-muted" style={{ fontSize: 12, marginTop: -8, marginBottom: 16 }}>
            {serviceHealthy === null ? "Checking clinical trials sidecar readiness..." : MATCHING_SERVICE_UNAVAILABLE_COPY}
          </div>
        )}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <StatusChip status={trialId ? "met" : "unknown"} label={trialId ? "Trial Selected" : "No Trial Selected"} size="sm" />
          <StatusChip status="source" label={populationRegistryLabel(populationMode)} size="sm" />
          {catalogStageScopeLabel && <StatusChip status="source" label={catalogStageScopeLabel} size="sm" />}
          {selectedTrialStageTargetWindow && (
            <StatusChip status="source" label={humanizeStageTargetWindow(selectedTrialStageTargetWindow)} size="sm" />
          )}
        </div>
        {matchError && (
          <div className="pds-card" style={{ padding: 12, marginTop: 12, color: "#dc2626" }}>{matchError}</div>
        )}
      </div>
    );
  };

  const renderCanvasContent = () => {
    if (canvasTab === "overview") return renderCanvasOverview();
    if (canvasTab === "criteria") {
      return direction === "patient_to_trials" ? renderRightP2T() : renderRightT2P();
    }
    return null;
  };

  /* =========================================================================
     Main render
     ========================================================================= */

  const canvasTabs = direction === "patient_to_trials"
    ? [
        { id: "overview", label: "Overview" },
        { id: "criteria", label: "Criterion Trace" },
      ]
    : [
        { id: "overview", label: "Overview" },
        { id: "criteria", label: "Clinical Trace" },
      ];

  const railLabel = matchPayload
    ? `Results (${matchResults.length})`
    : direction === "patient_to_trials" ? "Patient" : "Trial";

  const tabStyle = (active) => ({
    fontSize: 13,
    fontWeight: active ? 700 : 400,
    color: active ? "#0f172a" : "#64748b",
    background: "none",
    border: "none",
    borderBottom: active ? "2px solid #0f172a" : "2px solid transparent",
    padding: "10px 16px",
    cursor: "pointer",
    whiteSpace: "nowrap",
  });

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {/* ── Fixed top bar ── */}
      <div style={{ flexShrink: 0 }}>
        {renderGuidedStart()}
        {(hasLockedScope || showcaseCase || benchmarkPackId) && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 10, padding: "6px 0", borderBottom: "1px solid #e2e8f0" }}>
            {scopeSummary.map((item) => (
              <StatusChip key={`${item.label}:${item.value}`} status={item.status} label={`${item.label}: ${item.value}`} size="sm" />
            ))}
            {showcaseCase && <StatusChip status="canonical" label={`Showcase: ${String(showcaseCase).replace(/_/g, " ")}`} size="sm" />}
            {benchmarkPackId && <StatusChip status="source" label={`Pack: ${benchmarkPackId}`} size="sm" />}
            {hasLockedScope && (
              <button type="button" className="pds-btn pds-btn--ghost" style={{ fontSize: 11, padding: "2px 8px", marginLeft: "auto" }} onClick={clearMatchingScope}>
                Reset
              </button>
            )}
          </div>
        )}
        {renderTopControls()}
      </div>

      {/* ── Canvas workspace ── */}
      <div
        style={{
          display: "flex",
          height: "calc(100vh - 360px)",
          minHeight: 560,
          border: "1px solid #e2e8f0",
          borderRadius: 10,
          overflow: "hidden",
          background: "#fff",
          marginBottom: 24,
        }}
      >
        {/* Left rail */}
        <div
          style={{
            width: railCollapsed ? 44 : 280,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            borderRight: "1px solid #e2e8f0",
            transition: "width 0.18s ease",
            overflow: "hidden",
            background: "#f8fafc",
          }}
        >
          {/* Rail header */}
          <div
            style={{
              flexShrink: 0,
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: railCollapsed ? "10px 8px" : "10px 12px",
              borderBottom: "1px solid #e2e8f0",
              minHeight: 42,
            }}
          >
            <button
              type="button"
              onClick={() => setRailCollapsed((p) => !p)}
              style={{
                background: "none",
                border: "1px solid #cbd5e1",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: 14,
                lineHeight: 1,
                padding: "3px 7px",
                color: "#475569",
                flexShrink: 0,
              }}
              title={railCollapsed ? "Expand panel" : "Collapse panel"}
            >
              {railCollapsed ? "›" : "‹"}
            </button>
            {!railCollapsed && (
              <span style={{ fontSize: 12, fontWeight: 700, color: "#0f172a", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {railLabel}
              </span>
            )}
          </div>

          {/* Rail scrollable content */}
          {!railCollapsed && (
            <div style={{ flex: 1, overflowY: "auto", padding: "10px 10px" }}>
              {renderRailContent()}
            </div>
          )}
        </div>

        {/* Main canvas */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
          {/* Tab bar */}
          <div
            style={{
              flexShrink: 0,
              display: "flex",
              borderBottom: "1px solid #e2e8f0",
              padding: "0 8px",
              background: "#fff",
              overflowX: "auto",
            }}
          >
            {canvasTabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                style={tabStyle(canvasTab === tab.id)}
                onClick={() => setCanvasTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Canvas scrollable content */}
          <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>
            {renderCanvasContent()}
          </div>
        </div>
      </div>

      {renderTrialWorkspace()}
    </div>
  );
}
