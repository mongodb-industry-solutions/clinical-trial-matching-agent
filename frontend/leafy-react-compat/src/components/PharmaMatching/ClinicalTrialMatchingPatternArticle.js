import React from "react";

const processSteps = [
  {
    step: "1",
    title: "Ingest breast-cancer studies from ClinicalTrials.gov",
    body:
      "The catalog starts with source studies synced from ClinicalTrials.gov. We preserve the original trial payload, but we do not let matching execute directly against unstructured eligibility prose.",
  },
  {
    step: "2",
    title: "Transform raw eligibility into a governed canonical contract",
    body:
      "Eligibility text is decomposed into criterion objects with stable IDs, typed categories, computability flags, hard-filter hints, and standards mappings. This is the moment where the runtime becomes more deterministic: the matcher stops reasoning over anonymous paragraphs and starts reasoning over explicit rule objects.",
  },
  {
    step: "3",
    title: "Assemble one patient record object",
    body:
      "Patient truth stays in canonical FHIR resources, while the active Leafy screening episode adds current narrative and imaging context. Both are rendered through one reusable patient EMR object so the same patient looks the same in Pharma Navigator, PatientQuestion, and screening views.",
  },
  {
    step: "4",
    title: "Materialize deterministic oncology facts",
    body:
      "FHIR resources and screening evidence are normalized into governed oncology fact families such as receptor status, stage or extent, treatment history, performance status, labs, and demographics. This is the patient-side contract used by the matcher.",
  },
  {
    step: "5",
    title: "Run matching criterion by criterion",
    body:
      "The runtime first applies safe hard filters, then uses retrieval only to improve recall, and finally evaluates each inclusion and exclusion criterion independently as pass, fail, or unknown with provenance.",
  },
  {
    step: "6",
    title: "Store scores, runs, and improvement signals",
    body:
      "Every governed run can be measured and persisted. The control plane stores evaluation scores, dimension breakdowns, recent run history, and improvement drift so the system can be improved deliberately instead of heuristically.",
  },
];

const innovationPoints = [
  "One canonical MongoDB document model can hold raw FHIR truth, the governed trial contract, deterministic oncology facts, semantic retrieval projections, and run history without forcing separate storage systems.",
  "The matcher is semantic-first, not retrieval-first. Retrieval helps recall, but it does not decide eligibility by itself.",
  "The system is ontology-aware and standards-aware at the same time: patient truth stays FHIR-first, while the trial-side contract is CDISC-aligned and criterion-centric.",
  "The UI can show not only what matched, but why it matched, what evidence was found, and what still remains unknown.",
];

const qualityLoop = [
  {
    title: "Deterministic before probabilistic",
    body:
      "The product tries to satisfy criteria from explicit structured evidence first. Narrative review and retrieval are supporting layers, not the main contract.",
  },
  {
    title: "Unknown is honest, not hidden",
    body:
      "If the system does not have enough evidence for a rule, it returns unknown and surfaces the gap instead of blending it away inside a similarity score.",
  },
  {
    title: "Every run can be scored",
    body:
      "The control plane stores evaluation runs with overall score, dimension-level scores, and quality posture. That makes the process reproducible and measurable over time.",
  },
  {
    title: "Improvements are also tracked",
    body:
      "Improvement runs capture what changed after contract updates, ontology work, or data enrichment, so the team can see whether the matcher is actually getting better.",
  },
];

const mongodbFit = [
  "The same document model can preserve the original trial payload, store the canonical rule contract, persist FHIR truth, and keep evaluation history without forcing the team to duplicate logic across storage layers.",
  "MongoDB makes it practical to keep raw resources, normalized facts, search-ready projections, and control-plane run documents close together while still serving different product surfaces from the same operational base.",
  "Because scoring and improvement runs are also stored, the platform can show not only what the matcher decided, but whether the system is becoming more deterministic and trustworthy release after release.",
];

const collections = [
  {
    name: "hc_fhir_synthea_r4_sep2019_canonical.fhir_resources",
    icon: "FHIR",
    description:
      "Canonical longitudinal patient truth. Full polymorphic FHIR resources remain available for exchange, search, and deterministic evidence building.",
    shape: {
      resourceType: "Observation",
      logicalRef: "Observation/abc123",
      compartments: { patientRefs: ["Patient/123"] },
      resource: {
        status: "final",
        code: { coding: [{ system: "http://loinc.org", code: "89247-1" }] },
      },
    },
  },
  {
    name: "demo_hc_virtual_hospital.enriched_appointments",
    icon: "APPT",
    description:
      "Operational appointment context for the current screening flow, including the embedded authoritative Synthea patient reference when screening is recorded.",
    shape: {
      appointmentId: "661cc9...",
      patientId: "69a5...",
      syntheaPatientRef: "Patient/123",
      status: "fulfilled",
      appointmentType: "screening",
    },
  },
  {
    name: "demo_hc_virtual_hospital.clinicalreports",
    icon: "NOTE",
    description:
      "Narrative screening evidence such as findings, conclusions, and clinical notes. This supplements the current episode; it does not replace longitudinal FHIR truth.",
    shape: {
      reportId: "rpt-001",
      appointmentId: "661cc9...",
      syntheaPatientRef: "Patient/123",
      conclusions: ["BI-RADS 4 suspicious finding"],
      clinicalNotes: ["Biopsy recommended"],
    },
  },
  {
    name: "demo_hc_virtual_hospital.pharma_clinical_trials",
    icon: "TRIAL",
    description:
      "The breast-cancer trial catalog enriched with the canonical trial contract and safe hard-filter definitions used during matching.",
    shape: {
      trialId: "NCT01234567",
      title: "Example Breast Cancer Study",
      trialPrefilterContract: { hardFilters: { sex: { anyOf: ["female"] }, age: { min: 18 } } },
      trialDefinitionCanonical: {
        contractVersion: "trial_definition_canonical_v1",
        criteria: [{ criterionId: "IE-INC-001", criterionCategory: "biomarker", computability: "deterministic" }],
      },
    },
  },
  {
    name: "demo_hc_virtual_hospital.pharma_patient_facts",
    icon: "FACT",
    description:
      "The deterministic oncology fact layer used by the matcher. This is the patient-side contract between raw FHIR resources and criterion adjudication.",
    shape: {
      patientRef: "Patient/123",
      factVersion: "oncology_facts_v1",
      factFamilies: {
        receptorStatus: [{ code: "HER2", value: "positive" }],
        stageOrExtent: [{ stage: "III" }],
        performanceStatus: [{ scale: "ECOG", value: 1 }],
      },
      sourceRefs: ["Observation/abc123", "Condition/def456"],
    },
  },
  {
    name: "demo_hc_virtual_hospital.pharma_patient_profiles",
    icon: "VEC",
    description:
      "The optional retrieval projection for Atlas Search and vectors. It improves recall, but it is not a source of truth and does not make the eligibility decision.",
    shape: {
      patientRef: "Patient/123",
      profileText: "Metastatic HR+ HER2- breast cancer ...",
      featureTerms: ["metastatic", "her2", "endocrine therapy"],
      vectorEmbedding: "[1024-dim]",
    },
  },
  {
    name: "demo_hc_virtual_hospital.pharma_evaluation_runs",
    icon: "RUN",
    description:
      "Stored control-plane evaluation runs with overall score, dimension breakdown, status, and audit context. These runs prove whether the runtime quality is improving or regressing.",
    shape: {
      runId: "eval-2026-03-29-001",
      mode: "runtime_snapshot",
      overview: { overallScore: 84.5, status: "partial" },
      dimensions: [{ id: "dataset_readiness", score: 90 }],
    },
  },
  {
    name: "demo_hc_virtual_hospital.pharma_improvement_runs",
    icon: "DRIFT",
    description:
      "Stored improvement snapshots that compare newer governed runs against prior baselines and show drift, progress, and unresolved backlog.",
    shape: {
      runId: "improve-2026-03-29-001",
      baselineRunId: "eval-2026-03-20-004",
      latestRunId: "eval-2026-03-29-001",
      drift: { overallScoreDelta: 3.5, deterministicCoverageDelta: 7.0 },
    },
  },
];

const standards = [
  "Primary matching storage is a canonical MongoDB contract, not raw ODM 2.0 JSON persistence.",
  "Study-definition posture is CDISC USDM aligned.",
  "Criterion identity is CDISC ODM inspired so each rule becomes a stable governed object.",
  "Criterion tabulation is CDISC SDTM TI inspired for criterion-level reporting.",
  "Patient screening outcome is CDISC IE compatible.",
  "Patient evidence remains FHIR-first and oncology facts can be normalized with mCODE, SNOMED CT, LOINC, RxNorm, UCUM, and TNM or AJCC concepts where available.",
];

function JsonShape({ value }) {
  return (
    <pre className="pharma-pattern-code">
      <code>{JSON.stringify(value, null, 2)}</code>
    </pre>
  );
}

export default function ClinicalTrialMatchingPatternArticle() {
  return (
    <section className="pharma-pattern-shell">
      <article className="pharma-pattern-article">
        <header className="pharma-pattern-header">
          <span className="pharma-pattern-kicker">Clinical Trial Matching · Reference Pattern</span>
          <h2>A practical and governed way to build clinical trial matching on MongoDB</h2>
          <p>
            The goal of this pattern is not only to match patients to trials. The real goal is to make the full process easier to
            trust, explain, improve, and operate. In practice that means we do not depend on raw trial prose, opaque retrieval
            scores, or hidden heuristics as the main contract.
          </p>
          <p>
            Instead, the runtime converts trial definitions into a governed canonical contract, keeps patient truth in FHIR,
            materializes deterministic oncology facts, and stores evaluation scores and improvement runs in the control plane.
            MongoDB works well here because the same document model can hold raw truth, semantic projections, governed contracts,
            run history, and UI-ready evidence without splitting the system across multiple storage patterns.
          </p>
        </header>

        <section className="pharma-pattern-section">
          <h3>Why this approach is different</h3>
          <div className="pharma-pattern-copy">
            <p>
              A typical trial-matching demo stops at retrieval: take a patient summary, search trial text, rerank, and present a
              score. That is useful for discovery, but it is not strong enough as a governed clinical-matching contract. This
              implementation goes further by separating discovery from adjudication. Retrieval is allowed to improve recall, but the
              product still has to explain why a patient matched or failed on each criterion.
            </p>
            <p>
              That separation is the main innovation. The trial side becomes a stable contract with criterion IDs, categories,
              computability, and standards mappings. The patient side becomes a reusable FHIR-first EMR object plus deterministic
              oncology facts. Once both sides are explicit, the runtime can evaluate inclusion and exclusion rules one by one,
              surface unknowns honestly, and persist the quality of the process in the control plane.
            </p>
          </div>
        </section>

        <section className="pharma-pattern-section">
          <h3>From ingestion to matching</h3>
          <div className="pharma-pattern-story">
            {processSteps.map((item) => (
              <div key={item.step} className="pharma-pattern-story-step">
                <div className="pharma-pattern-story-stepnum">{item.step}</div>
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.body}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="pharma-pattern-section">
          <h3>What makes the matcher more trustworthy</h3>
          <div className="pharma-pattern-copy">
            <p>
              The matcher improves quality by making more of the process explicit. A criterion can be deterministic, narrative, or
              unsupported. A patient fact can come from canonical FHIR truth, current screening context, or a clearly marked derived
              layer. A run can be scored, compared, and improved later. This is more work than a pure retrieval demo, but it is the
              difference between a useful prototype and a production-grade matching pattern.
            </p>
          </div>
          <ul className="pharma-pattern-bullets">
            {innovationPoints.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section className="pharma-pattern-section">
          <h3>How quality is improved over time</h3>
          <div className="pharma-pattern-copy">
            <p>
              The control plane is not just a monitoring screen. It is the mechanism that turns the matcher into an improving
              system. The runtime stores evaluation runs with score breakdowns, and it stores improvement runs that compare newer
              versions against prior baselines. That gives the team a concrete loop: detect weak criterion families, enrich data or
              ontologies, rerun evaluation, and prove whether quality improved.
            </p>
          </div>
          <div className="pharma-pattern-quality-loop">
            {qualityLoop.map((item) => (
              <div key={item.title} className="pharma-pattern-quality-item">
                <strong>{item.title}</strong>
                <p>{item.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="pharma-pattern-section">
          <h3>Why MongoDB fits this pattern</h3>
          <div className="pharma-pattern-copy">
            <p>
              This design works well on MongoDB because clinical trial matching is not one data shape. It needs raw trial payloads,
              canonical criterion objects, longitudinal FHIR resources, deterministic oncology facts, semantic retrieval support,
              and stored run history. A document model lets those layers stay connected without pretending they are the same thing.
            </p>
            <p>
              That matters for product quality. The UI can move from source trial text to canonical rules, from patient EMR to
              derived facts, and from a single match decision to stored evaluation runs, all without crossing disconnected systems.
              The result is easier to explain, easier to improve, and easier to operate.
            </p>
          </div>
          <ul className="pharma-pattern-bullets">
            {mongodbFit.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section className="pharma-pattern-section">
          <h3>What is stored in MongoDB</h3>
          <div className="pharma-pattern-copy">
            <p>
              The collections below show the practical runtime shape. The important point is not the exact field names. The
              important point is that operational truth, semantic retrieval support, deterministic facts, and run history all remain
              understandable in one document model.
            </p>
          </div>
          <div className="pharma-pattern-collections">
            {collections.map((item) => (
              <div key={item.name} className="pharma-pattern-collection">
                <div className="pharma-pattern-collection-head">
                  <span className="pharma-pattern-collection-icon">{item.icon}</span>
                  <div>
                    <strong>{item.name}</strong>
                  </div>
                </div>
                <p>{item.description}</p>
                <JsonShape value={item.shape} />
              </div>
            ))}
          </div>
        </section>

        <section className="pharma-pattern-section">
          <h3>Standards posture</h3>
          <div className="pharma-pattern-copy">
            <p>
              The standards posture is intentionally explicit. This runtime is CDISC-aligned and FHIR-first, but it does not claim
              something stronger than what is actually implemented. That matters if the goal is to show a good-practice pattern
              rather than make a vague standards claim.
            </p>
          </div>
          <ul className="pharma-pattern-bullets">
            {standards.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      </article>
    </section>
  );
}
