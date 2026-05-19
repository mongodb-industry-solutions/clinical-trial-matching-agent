const PROXY_PREFIX = "/api";

function buildProxyUrl(path) {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${PROXY_PREFIX}${normalized}`;
}

async function request(path, options = {}) {
  const res = await fetch(buildProxyUrl(path), {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  // Trial search (ClinicalTrials.gov remote)
  searchTrials: (body) =>
    request("/clinical-trials/search", { method: "POST", body: JSON.stringify(body) }),

  // Ingested trial catalog
  listTrials: (params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null))
    ).toString();
    return request(`/clinical-trials/trials${qs ? `?${qs}` : ""}`);
  },

  getTrial: (id) => request(`/clinical-trials/trials/${id}`),
  getTrialCriteria: (id) => request(`/clinical-trials/trials/${id}/criteria`),
  getCanonicalTree: (id) => request(`/clinical-trials/trials/${id}/canonical-tree`),

  // Ingest
  ingestTrial: (body) =>
    request("/clinical-trials/ingest", { method: "POST", body: JSON.stringify(body) }),
  bulkIngestTrials: (body) =>
    request("/clinical-trials/ingest/bulk", { method: "POST", body: JSON.stringify(body) }),

  // Match
  matchPatient: (patientRef, body = {}) =>
    request(`/clinical-trials/match/patient/${patientRef}`, { method: "POST", body: JSON.stringify(body) }),
  matchTrial: (trialId, body = {}) =>
    request(`/clinical-trials/match/trial/${trialId}`, { method: "POST", body: JSON.stringify(body) }),
  listMatchRuns: () => request("/clinical-trials/match-runs"),
  getMatchRun: (id) => request(`/clinical-trials/match-runs/${id}`),

  // Patients
  listPatients: (params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null && v !== ""))
    ).toString();
    return request(`/api/v1/patients/catalog${qs ? `?${qs}` : ""}`);
  },
  getPatientWorkspace: (ref, params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null))
    ).toString();
    return request(`/api/v1/patients/${encodeURIComponent(ref)}/workspace${qs ? `?${qs}` : ""}`);
  },
  getPatientEvidence: (ref, params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null))
    ).toString();
    return request(`/api/v1/patients/${encodeURIComponent(ref)}/evidence${qs ? `?${qs}` : ""}`);
  },
  getPatientMatchProfile: (ref, params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null))
    ).toString();
    return request(`/api/v1/patients/${encodeURIComponent(ref)}/match-profile${qs ? `?${qs}` : ""}`);
  },
  getRandomPatient: () => request("/api/v1/patients/random"),
  getPatientCoverage: () => request("/api/v1/patient-coverage"),
  getStorageOverview: () => request("/api/v1/workbench/storage"),
  runDemoScreeningEnrichment: (params = {}) => {
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v != null))
    ).toString();
    return request(`/api/v1/patients/enrichment/demo-screened${qs ? `?${qs}` : ""}`, { method: "POST" });
  },

  // Governance
  getBenchmarkLatest: () => request("/clinical-trials/benchmarks/micro/latest"),
  getFamilyCoverage: () => request("/clinical-trials/analytics/family-coverage"),
  getUnresolvedTop: () => request("/clinical-trials/analytics/unresolved-top"),
  getReviewQueue: () => request("/clinical-trials/review-queue"),
  getRuleFamilies: () => request("/clinical-trials/rule-families"),

  // Health
  health: () => request("/clinical-trials/health"),
};
