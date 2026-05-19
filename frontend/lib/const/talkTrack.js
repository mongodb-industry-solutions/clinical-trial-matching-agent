export const TALK_TRACK = [
  {
    heading: "Demo Narrative",
    content: [
      {
        heading: "Solution Overview",
        body: "The thesis is simple: traditional clinical trial matching answers which patients meet criteria, while hospital pharmacy adds the richer question of which patients are truly viable from a pharmacotherapeutic, operational, and patient-support perspective.",
      },
      {
        heading: "How to Demo",
        body: [
          "Start with a new sponsor trial and show how ClinicalTrials.gov content becomes a governed, versioned trial contract with explicit supported and unsupported criteria.",
          "Open a patient workspace and show the evidence profile, match profile, provenance, and any missing or conflicting facts.",
          "Show a simple cohort funnel: total cohort, diagnosis-compatible patients, biomarker-compatible patients, prior-line-compatible patients, clinically screened candidates, pharmacologically viable patients, and priority candidates for review.",
          "Run deterministic matching and focus on criterion-by-criterion outcomes: met, not met, unknown, questionnaire-fillable, and manual review.",
          "Add the pharmacy-aware layer by showing what still needs medication review, washout validation, patient confirmation, or operational support.",
          "End with a concrete pharmacy action plan rather than more automation.",
        ],
      },
      {
        heading: "What Makes It Different",
        body: [
          "Traditional trial matching finds eligible patients.",
          "This platform helps find viable patients.",
          "The first filter identifies candidates. The second filter, from hospital pharmacy, clarifies whether those candidates are realistic from the medication, operational, and support perspective.",
          "Social and burden signals are used to design support, not to silently exclude patients.",
          "The platform is intentionally honest about uncertainty and routes unsupported logic into review instead of invention.",
        ],
      },
    ],
  },
  {
    heading: "Behind the Scenes",
    content: [
      {
        heading: "Deterministic Architecture",
        body: [
          "Trial Contract Layer: ingests source studies, compiles canonical criteria, versions trial contracts, and preserves source traceability.",
          "Patient Evidence Layer: assembles governed patient evidence and a match-ready profile with completeness, conflict, and linkage state.",
          "Matching Runtime: evaluates supported criterion families deterministically and returns explicit adjudication states instead of opaque scores.",
          "Pharmacy Viability Layer: derives medication review needs, support needs, and operational risk from the deterministic result set.",
          "Governance Layer: tracks benchmark packs, unresolved clusters, review burden, and run-level explainability.",
        ],
      },
      {
        heading: "Bounded AI Role",
        body: [
          "AI can summarize the trial contract, explain deterministic results, and propose follow-up questions.",
          "AI does not infer missing biomarkers, override hard inclusion or exclusion criteria, or turn unsupported medication rules into approvals.",
          "The model only sees bounded artifacts such as published trial contracts, governed patient evidence, deterministic match rows, and structured questionnaire responses.",
        ],
      },
      {
        heading: "What Happens When The System Does Not Know",
        body: [
          "Missing evidence stays visible as unknown.",
          "Patient-confirmable facts surface as questionnaire-fillable gaps.",
          "Unsupported or ambiguous criteria remain manual review work.",
          "The goal is confidence, uncertainty, and human review, not magic.",
        ],
      },
    ],
  },
  {
    heading: "Why MongoDB?",
    content: [
      {
        heading: "Unified Knowledge Layer",
        body: "MongoDB fits this pattern because the product needs one operational store for raw trial source versions, canonical contracts, patient evidence profiles, match runs, review actions, and sponsor-facing cohort analytics.",
      },
      {
        heading: "Mixed Data With Provenance",
        body: [
          "Clinical trial criteria, patient evidence, questionnaire responses, and audit traces do not fit neatly into one rigid relational shape.",
          "Document models let the product preserve structured facts, semi-structured evidence, and versioned explainability side by side.",
        ],
      },
      {
        heading: "Operational Retrieval And Governance",
        body: [
          "Atlas Search and aggregation support broad discovery and cohort analytics.",
          "The deterministic matcher and governed run artifacts keep eligibility decisions explainable and replayable.",
          "The result is an AI-capable architecture where retrieval is constrained by trusted operational records rather than open-ended prompting.",
        ],
      },
    ],
  },
];
