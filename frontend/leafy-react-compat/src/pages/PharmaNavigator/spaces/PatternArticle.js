import React, { useEffect, useState } from "react";
import {
  SpaceHeader, StatusChip, TransformationArrow,
  CriterionCard, SectionDivider,
} from "../../../components/PharmaDesignSystem/PharmaDesignSystem";
import { APIGetClinicalTrialMicroBenchmarkLatest } from "../../../services/APIService";

/* ---------- Pipeline step data ------------------------------------------ */

const PIPELINE_STEPS = [
  { number: 1, title: "Trial Ingestion", borderColor: "var(--pharma-source)",
    description: "ClinicalTrials.gov studies are fetched, parsed, and compiled into canonical contracts with stable criterion IDs, typed categories, and computability flags.",
    link: { label: "See in Clinical Trial Ingestion", space: "ingestion" } },
  { number: 2, title: "Patient Data", borderColor: "var(--pharma-source)",
    description: "Heterogeneous patient data is reconciled into curated evidence profiles organized by match-relevant families. Identity linkage, conflict detection, and gap analysis happen before any trial is considered.",
    link: { label: "See in Patient Data", space: "patient" } },
  { number: 3, title: "Deterministic Prefilter", borderColor: "var(--pharma-canonical)",
    description: "Safe hard filters (age, sex, cancer type, performance status) eliminate obviously unsuitable matches before deeper evaluation. This reduces the search space without any probabilistic uncertainty.",
    link: { label: "See in Matching Studio", space: "matching" } },
  { number: 4, title: "Criterion Adjudication", borderColor: "var(--pharma-canonical)",
    description: "Every inclusion and exclusion criterion is evaluated independently against patient evidence. Each evaluation produces a deterministic status: met, not met, unknown, or manual review required.",
    link: null },
  { number: 5, title: "Scoring & Ranking", borderColor: "var(--pharma-runtime)",
    description: "Results are ranked by deterministic pass coverage, exclusion triggers, unknown burden, and blocker severity — not by a hidden composite score.",
    link: null },
  { number: 6, title: "Route & Trace", borderColor: "var(--pharma-runtime)",
    description: "Every decision is traceable: which criterion, which evaluator, which patient evidence, which status. The control plane tracks coverage, blockers, and benchmark readiness.",
    link: { label: "See in Control Plane", space: "control" } },
];

/* ---------- MongoDB advantage cards --------------------------------------- */

const MONGO_ADVANTAGES = [
  { title: "Flexible Document Model", body: "Trial criteria vary wildly in structure. MongoDB stores each criterion as a rich document with stable IDs, typed categories, and compiled predicates — no rigid relational schema required." },
  { title: "Operational + Analytical in One", body: "Patient facts, trial contracts, and match results live in the same database. The matching engine reads and writes without ETL delays." },
  { title: "Atlas Search for Hybrid Retrieval", body: "When deterministic prefilters are not enough, Atlas vector search provides semantic recall — but never decides eligibility." },
  { title: "Governed Collections", body: "Each transformation stage persists to a governed collection. pharma_clinical_trials stores trial contracts. pharma_patient_facts stores curated evidence. match_runs stores every decision trace." },
];

/* ---------- Trust pillars ------------------------------------------------ */

const TRUST_PILLARS = [
  { title: "Deterministic Logic", body: "No black box. Every criterion evaluation uses a named evaluator with a clear predicate." },
  { title: "Evidence Provenance", body: "Every patient fact traces back to its source: FHIR, clinical report, or linked record." },
  { title: "Explicit Gaps", body: "Unknown is a first-class status. The system never guesses — it tells you what it doesn't know." },
  { title: "Benchmark Gate", body: "The control plane tracks whether the runtime meets authoritative standards before any result is trusted." },
];

const showcaseLabel = (value) =>
  String(value || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

/* --------------------------------------------------------------------------
   Component
   -------------------------------------------------------------------------- */

function PatternArticle({ navigateSpace }) {
  const [benchmarkBundle, setBenchmarkBundle] = useState(null);

  useEffect(() => {
    let cancelled = false;
    APIGetClinicalTrialMicroBenchmarkLatest()
      .then((payload) => {
        if (!cancelled) setBenchmarkBundle(payload?.benchmark || null);
      })
      .catch(() => {
        if (!cancelled) setBenchmarkBundle(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const showcaseCases = benchmarkBundle?.showcase?.cases || {};

  return (
    <div className="pds-fade-in">
      <SpaceHeader
        title="The Deterministic Clinical Trial Matching Pattern"
        subtitle="Architecture reference — how MongoDB powers governed, criterion-by-criterion matching"
      />

      {/* ----------------------------------------------------------------
          Section 1 — The Challenge
          ---------------------------------------------------------------- */}
      <div className="pds-card" style={{ marginTop: 24 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 16px", color: "var(--pharma-text)" }}>
          Why Clinical Trial Matching Is Hard
        </h2>

        <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--pharma-text-secondary)", margin: "0 0 12px" }}>
          Trial eligibility is written in unstructured prose — natural language paragraphs that mix
          medical terminology, numerical thresholds, temporal logic, and exceptions. Patient data, meanwhile,
          is scattered across heterogeneous sources: EHRs, lab systems, imaging reports, and questionnaires.
        </p>
        <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--pharma-text-secondary)", margin: "0 0 12px" }}>
          Matching a patient to a trial requires both medical reasoning and data engineering. Most existing
          solutions use probabilistic scoring — a single number that obscures which criteria passed, which
          failed, and which could not be evaluated at all.
        </p>
        <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--pharma-text-secondary)", margin: 0 }}>
          This pattern takes a different approach: <strong>deterministic, criterion-by-criterion matching
          with full evidence trace</strong>. Every criterion is compiled into canonical logic, evaluated
          independently, and traced back to the patient data that informed the decision. Nothing is hidden.
        </p>
      </div>

      <SectionDivider />

      {Object.keys(showcaseCases).length > 0 && (
        <>
          <div className="pds-card">
            <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 12px", color: "var(--pharma-text)" }}>
              Frozen Showcase Cases
            </h2>
            <p style={{ fontSize: 14, lineHeight: 1.7, color: "var(--pharma-text-secondary)", margin: "0 0 16px" }}>
              These cases come from the frozen micro-benchmark pack and are the primary narrative for the new canonical path.
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
              {Object.entries(showcaseCases).map(([caseKey, casePayload]) => (
                <div key={caseKey} className="pds-card" style={{ margin: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                    <StatusChip status="canonical" label={showcaseLabel(caseKey)} size="sm" />
                    <StatusChip status="source" label={casePayload?.trialId || casePayload?.trialSummary?.trialId || "Trial"} size="sm" />
                  </div>
                  <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--pharma-text-secondary)", margin: "0 0 12px" }}>
                    {casePayload?.whyThisShowcase || "Frozen showcase case"}
                  </p>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button
                      className="pds-btn pds-btn--ghost"
                      type="button"
                      onClick={() => navigateSpace("ingestion", {
                        trialId: casePayload?.trialId || casePayload?.trialSummary?.trialId || "",
                        patientRef: casePayload?.patientRef || casePayload?.patientSummary?.patientRef || "",
                        direction: casePayload?.direction || "patient_to_trials",
                        populationMode: casePayload?.populationMode || "authoritative_oncology",
                        benchmarkPackId: casePayload?.benchmarkPackId || benchmarkBundle?.benchmarkPackId || "",
                        showcaseCase: casePayload?.showcaseCase || caseKey,
                        launchSource: "pattern_article",
                      })}
                    >
                      Trial
                    </button>
                    <button
                      className="pds-btn pds-btn--ghost"
                      type="button"
                      onClick={() => navigateSpace("patient", {
                        trialId: casePayload?.trialId || casePayload?.trialSummary?.trialId || "",
                        patientRef: casePayload?.patientRef || casePayload?.patientSummary?.patientRef || "",
                        direction: casePayload?.direction || "patient_to_trials",
                        populationMode: casePayload?.populationMode || "authoritative_oncology",
                        benchmarkPackId: casePayload?.benchmarkPackId || benchmarkBundle?.benchmarkPackId || "",
                        showcaseCase: casePayload?.showcaseCase || caseKey,
                        launchSource: "pattern_article",
                      })}
                    >
                      Patient
                    </button>
                    <button
                      className="pds-btn pds-btn--primary"
                      type="button"
                      onClick={() => navigateSpace("matching", {
                        trialId: casePayload?.trialId || casePayload?.trialSummary?.trialId || "",
                        patientRef: casePayload?.patientRef || casePayload?.patientSummary?.patientRef || "",
                        direction: casePayload?.direction || "patient_to_trials",
                        populationMode: casePayload?.populationMode || "authoritative_oncology",
                        benchmarkPackId: casePayload?.benchmarkPackId || benchmarkBundle?.benchmarkPackId || "",
                        showcaseCase: casePayload?.showcaseCase || caseKey,
                        launchSource: "pattern_article",
                      })}
                    >
                      Match
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <SectionDivider />
        </>
      )}

      {/* ----------------------------------------------------------------
          Section 2 — The Object Model
          ---------------------------------------------------------------- */}
      <div className="pds-card">
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 16px", color: "var(--pharma-text)" }}>
          Source &rarr; Canonical Logic &rarr; Runtime
        </h2>

        <div style={{ display: "flex", justifyContent: "center", margin: "16px 0 24px" }}>
          <TransformationArrow />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
          {/* Source column */}
          <div style={{
            background: "var(--pharma-source-bg)", borderRadius: "var(--pharma-radius-sm)", padding: 16,
          }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: "0 0 8px", color: "var(--pharma-text)" }}>
              What enters the system
            </h3>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.8, color: "var(--pharma-text-secondary)" }}>
              <li>Trial prose from ClinicalTrials.gov</li>
              <li>Patient EHR from FHIR</li>
              <li>Screening reports</li>
              <li>Lab results</li>
            </ul>
          </div>

          {/* Canonical Logic column */}
          <div style={{
            background: "var(--pharma-canonical-bg)", borderRadius: "var(--pharma-radius-sm)", padding: 16,
          }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: "0 0 8px", color: "var(--pharma-text)" }}>
              What the system compiles
            </h3>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.8, color: "var(--pharma-text-secondary)" }}>
              <li>Parsed criterion trees</li>
              <li>Family-assigned rules</li>
              <li>Compiled predicates</li>
              <li>Curated evidence profiles</li>
            </ul>
          </div>

          {/* Runtime column */}
          <div style={{
            background: "var(--pharma-runtime-bg)", borderRadius: "var(--pharma-radius-sm)", padding: 16,
          }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, margin: "0 0 8px", color: "var(--pharma-text)" }}>
              What the system evaluates
            </h3>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.8, color: "var(--pharma-text-secondary)" }}>
              <li>Prefilter gates</li>
              <li>Criterion-by-criterion evaluation</li>
              <li>Evidence completeness</li>
              <li>Decision + trace</li>
            </ul>
          </div>
        </div>

        <p style={{
          fontSize: 13, fontStyle: "italic", color: "var(--pharma-text-muted)",
          margin: "16px 0 0", textAlign: "center",
        }}>
          The core insight: the system never evaluates against raw text.
        </p>
      </div>

      <SectionDivider />

      {/* ----------------------------------------------------------------
          Section 3 — The Pipeline
          ---------------------------------------------------------------- */}
      <div className="pds-card">
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 20px", color: "var(--pharma-text)" }}>
          Six-Stage Transformation Pipeline
        </h2>

        <div style={{ position: "relative", paddingLeft: 28 }}>
          {/* Vertical timeline line */}
          <div style={{
            position: "absolute", left: 13, top: 8, bottom: 8, width: 2,
            background: "linear-gradient(to bottom, var(--pharma-source), var(--pharma-canonical), var(--pharma-runtime))",
            borderRadius: 1,
          }} />

          {PIPELINE_STEPS.map((step, idx) => (
            <div
              key={step.number}
              style={{
                position: "relative",
                marginBottom: idx < PIPELINE_STEPS.length - 1 ? 16 : 0,
              }}
            >
              {/* Timeline dot */}
              <div style={{
                position: "absolute", left: -22, top: 14, width: 10, height: 10,
                borderRadius: "50%", background: step.borderColor, zIndex: 1,
              }} />

              <div
                className="pds-card"
                style={{
                  borderLeft: `3px solid ${step.borderColor}`,
                  margin: 0,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                  <StatusChip status="not_patient_rule" label={String(step.number)} size="sm" />
                  <span style={{ fontSize: 15, fontWeight: 700, color: "var(--pharma-text)" }}>
                    {step.title}
                  </span>
                </div>
                <p style={{ fontSize: 14, lineHeight: 1.6, color: "var(--pharma-text-secondary)", margin: 0 }}>
                  {step.description}
                </p>
                {step.link && (
                  <button
                    className="pds-btn pds-btn--ghost"
                    style={{ marginTop: 10, padding: "4px 0", fontSize: 13 }}
                    onClick={() => navigateSpace(step.link.space)}
                    type="button"
                  >
                    {step.link.label} &rarr;
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      <SectionDivider />

      {/* ----------------------------------------------------------------
          Section 4 — Why MongoDB
          ---------------------------------------------------------------- */}
      <div className="pds-card">
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 16px", color: "var(--pharma-text)" }}>
          Why MongoDB Fits This Pattern
        </h2>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12 }}>
          {MONGO_ADVANTAGES.map((adv) => (
            <div
              key={adv.title}
              style={{
                background: "var(--pharma-bg)", borderRadius: "var(--pharma-radius-sm)",
                padding: 16, border: "1px solid var(--pharma-border)",
              }}
            >
              <h3 style={{ fontSize: 14, fontWeight: 700, margin: "0 0 6px", color: "var(--pharma-text)" }}>
                {adv.title}
              </h3>
              <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--pharma-text-secondary)", margin: 0 }}>
                {adv.body}
              </p>
            </div>
          ))}
        </div>
      </div>

      <SectionDivider />

      {/* ----------------------------------------------------------------
          Section 5 — Canonical Matching Explained
          ---------------------------------------------------------------- */}
      <div className="pds-card">
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 12px", color: "var(--pharma-text)" }}>
          What Canonical Matching Means
        </h2>

        <p style={{ fontSize: 14, lineHeight: 1.6, color: "var(--pharma-text-secondary)", margin: "0 0 16px" }}>
          Consider one criterion: <strong>"Minimum age 18 years"</strong>
        </p>

        <div style={{ display: "flex", justifyContent: "center", margin: "0 0 20px" }}>
          <TransformationArrow compact />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16, marginBottom: 20 }}>
          {/* Source */}
          <div style={{
            background: "var(--pharma-source-bg)", borderRadius: "var(--pharma-radius-sm)",
            padding: 14, border: "1px solid var(--pharma-border)",
          }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", color: "var(--pharma-text-muted)", marginBottom: 8 }}>
              Source
            </div>
            <code style={{
              fontSize: 13, color: "var(--pharma-text)", background: "rgba(0,0,0,0.04)",
              padding: "4px 8px", borderRadius: 4, display: "block",
            }}>
              Minimum age 18 years
            </code>
          </div>

          {/* Canonical Logic */}
          <div style={{
            background: "var(--pharma-canonical-bg)", borderRadius: "var(--pharma-radius-sm)",
            padding: 14, border: "1px solid var(--pharma-border)",
          }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", color: "var(--pharma-text-muted)", marginBottom: 8 }}>
              Canonical Logic
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.8, color: "var(--pharma-text)" }}>
              <div><strong>Family:</strong> demographics.age.min</div>
              <div><strong>Predicate:</strong> demographics.age_years &gt;= 18</div>
              <div><strong>Computability:</strong> deterministic</div>
            </div>
          </div>

          {/* Runtime */}
          <div style={{
            background: "var(--pharma-runtime-bg)", borderRadius: "var(--pharma-radius-sm)",
            padding: 14, border: "1px solid var(--pharma-border)",
          }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", color: "var(--pharma-text-muted)", marginBottom: 8 }}>
              Runtime
            </div>
            <div style={{ fontSize: 12, color: "var(--pharma-text-secondary)", marginBottom: 4 }}>
              Live component preview:
            </div>
          </div>
        </div>

        {/* Live CriterionCard demo */}
        <CriterionCard
          status="met"
          title="Age Requirement (minimum)"
          reason="Patient age 52 meets minimum threshold 18"
          familyId="demographics.age.min"
          patientValue="52"
          trialValue={"\u2265 18"}
          evaluator="age_min_evaluator"
          rawText="Minimum age 18 years"
        />

        <p style={{
          fontSize: 13, fontStyle: "italic", color: "var(--pharma-text-muted)",
          margin: "16px 0 0", textAlign: "center",
        }}>
          Every criterion goes through this exact transformation. The system stores the canonical form,
          evaluates deterministically, and traces every decision.
        </p>
      </div>

      <SectionDivider />

      {/* ----------------------------------------------------------------
          Section 6 — How Trust Is Built
          ---------------------------------------------------------------- */}
      <div className="pds-card">
        <h2 style={{ fontSize: 20, fontWeight: 700, margin: "0 0 16px", color: "var(--pharma-text)" }}>
          Building Trust Through Proof
        </h2>

        <div style={{
          display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12,
        }}>
          {TRUST_PILLARS.map((pillar) => (
            <div
              key={pillar.title}
              style={{
                background: "var(--pharma-bg)", borderRadius: "var(--pharma-radius-sm)",
                padding: 16, border: "1px solid var(--pharma-border)", textAlign: "center",
              }}
            >
              <h3 style={{ fontSize: 14, fontWeight: 700, margin: "0 0 8px", color: "var(--pharma-text)" }}>
                {pillar.title}
              </h3>
              <p style={{ fontSize: 13, lineHeight: 1.5, color: "var(--pharma-text-secondary)", margin: 0 }}>
                {pillar.body}
              </p>
            </div>
          ))}
        </div>
      </div>

      <SectionDivider />

      {/* ----------------------------------------------------------------
          Footer — Navigation
          ---------------------------------------------------------------- */}
      <div className="pds-card" style={{ textAlign: "center" }}>
        <p style={{ fontSize: 15, fontWeight: 600, color: "var(--pharma-text)", margin: "0 0 14px" }}>
          Explore the live product:
        </p>
        <div style={{ display: "flex", justifyContent: "center", gap: 12, flexWrap: "wrap" }}>
          <button
            className="pds-btn pds-btn--ghost"
            onClick={() => navigateSpace("ingestion")}
            type="button"
          >
            Clinical Trial Ingestion &rarr;
          </button>
          <button
            className="pds-btn pds-btn--ghost"
            onClick={() => navigateSpace("patient")}
            type="button"
          >
            Patient Data &rarr;
          </button>
          <button
            className="pds-btn pds-btn--ghost"
            onClick={() => navigateSpace("matching")}
            type="button"
          >
            Matching Studio &rarr;
          </button>
          <button
            className="pds-btn pds-btn--ghost"
            onClick={() => navigateSpace("control")}
            type="button"
          >
            Control Plane &rarr;
          </button>
        </div>
      </div>
    </div>
  );
}

export default PatternArticle;
