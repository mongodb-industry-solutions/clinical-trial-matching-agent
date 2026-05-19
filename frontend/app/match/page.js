import { redirect } from "next/navigation";

function firstValue(value) {
  if (Array.isArray(value)) return value[0] || "";
  return value || "";
}

function normalizeDirection(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "trial" || normalized === "trial_to_patients") {
    return "trial_to_patients";
  }
  return "patient_to_trials";
}

function normalizePopulationMode(value) {
  return String(value || "").trim().toLowerCase() === "pharmacy_prescreen"
    ? "pharmacy_prescreen"
    : "authoritative_oncology";
}

export default async function MatchPage({ searchParams }) {
  const params = await searchParams;
  const direction = normalizeDirection(firstValue(params?.mode));
  const patientRef = firstValue(params?.patientRef) || firstValue(params?.patient) || "";
  const trialId = firstValue(params?.trialId) || firstValue(params?.trial) || "";
  const populationMode = normalizePopulationMode(firstValue(params?.populationMode));

  const next = new URLSearchParams();
  next.set("space", "matching");
  next.set("direction", direction);
  next.set("populationMode", populationMode);
  if (patientRef) next.set("patientRef", patientRef);
  if (trialId) next.set("trialId", trialId);

  redirect(`/pharmaNavigator?${next.toString()}`);
}
