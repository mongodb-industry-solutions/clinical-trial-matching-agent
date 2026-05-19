"use client";

import React, { useMemo } from "react";
import Icon from "@leafygreen-ui/icon";

const SPACE_META = {
  landing: {
    navLabel: "Navigator",
    title: "Clinical Trials Navigator",
    subtitle:
      "Work through one continuous flow: ingest clinical trials, inspect the patient data we actually have, and run matching in both directions.",
  },
  ingestion: {
    navLabel: "Clinical Trial Ingestion",
    title: "Clinical Trial Ingestion",
    subtitle:
      "Search ClinicalTrials.gov, ingest studies, and compile canonical inclusion and exclusion structures ready for deterministic matching.",
  },
  patient: {
    navLabel: "Patient Data",
    title: "Patient Data",
    subtitle:
      "Inspect one screened patient, observe the evidence we have, and understand how it becomes a match-ready patient graph.",
  },
  matching: {
    navLabel: "Clinical Trial Matching",
    title: "Clinical Trial Matching",
    subtitle:
      "Run criterion-by-criterion matching for one patient across many trials or for one trial across many patients.",
  },
  control: {
    navLabel: "Proof",
    title: "Proof & Governance",
    subtitle:
      "Monitor readiness, unresolved criteria, and benchmark posture when we need to validate the engine more deeply.",
  },
  pattern: {
    navLabel: "Pattern",
    title: "Pattern Library",
    subtitle: "Reference patterns and architectural framing for the workbench.",
  },
};

const PRIMARY_FLOW = [
  { key: "ingestion", number: "1" },
  { key: "patient", number: "2" },
  { key: "matching", number: "3" },
];

function FlowStep({ active, number, label, onClick }) {
  return (
    <button
      type="button"
      className={`pharma-flow-shell__step${active ? " is-active" : ""}`}
      onClick={onClick}
    >
      <span className="pharma-flow-shell__step-number">{number}</span>
      <span className="pharma-flow-shell__step-label">{label}</span>
    </button>
  );
}

function ContextChip({ icon, label, value }) {
  return (
    <div className="pharma-flow-shell__context-chip">
      <span className="pharma-flow-shell__context-icon">
        <Icon glyph={icon} size="small" role="presentation" />
      </span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function humanizeDirection(value) {
  return String(value || "").trim() === "trial_to_patients"
    ? "Trial to Patients"
    : "Patient to Trials";
}

function patientRegistryLabel(populationMode) {
  return String(populationMode || "").trim() === "pharmacy_prescreen"
    ? "Unified patient graph · screening enriched"
    : "Unified patient graph";
}

const COMPACT_SPACES = new Set(["matching", "ingestion", "patient", "control"]);

export default function AtlasWorkbenchShell({
  space,
  lens,
  selectedPatientRef,
  selectedTrialId,
  matchDirection,
  populationMode,
  benchmarkPackId,
  navigateSpace,
  children,
}) {
  const compact = COMPACT_SPACES.has(space);
  const meta = SPACE_META[space] || SPACE_META.landing;

  const contextChips = useMemo(
    () => [
      {
        icon: "Database",
        label: "Trial",
        value: selectedTrialId || "Select one clinical trial",
      },
      {
        icon: "Person",
        label: "Patient",
        value: selectedPatientRef || "Select one patient",
      },
      {
        icon: "Refresh",
        label: "Workflow",
        value: humanizeDirection(matchDirection),
      },
      {
        icon: "Charts",
        label: "Patient Registry",
        value: patientRegistryLabel(populationMode),
      },
      {
        icon: "Sparkle",
        label: "Focus",
        value: lens === "discovery" ? "Discovery" : "Breast oncology AI",
      },
      {
        icon: "Gauge",
        label: "Pack",
        value: benchmarkPackId || "Live runtime context",
      },
    ],
    [
      benchmarkPackId,
      lens,
      matchDirection,
      populationMode,
      selectedPatientRef,
      selectedTrialId,
    ],
  );

  return (
    <div className="pharma-flow-shell">
      <header className="pharma-flow-shell__header">
        <div className="pharma-flow-shell__brand">
          <div className="pharma-flow-shell__brand-mark">
            <Icon glyph="ActivityFeed" size="small" role="presentation" />
          </div>
          <div>
            <span className="pharma-flow-shell__eyebrow">
              Clinical Trial Matching
            </span>
            <strong>Clinical Trials Navigator</strong>
          </div>
        </div>
        {space !== "landing" ? (
          <button
            type="button"
            className="pharma-flow-shell__overview-link"
            onClick={() => navigateSpace("landing")}
          >
            Back to Overview
          </button>
        ) : null}
      </header>

      {!compact && (
        <section className="pharma-flow-shell__hero">
          <div className="pharma-flow-shell__hero-copy">
            <span className="pharma-flow-shell__eyebrow">{meta.navLabel}</span>
            <h1>{meta.title}</h1>
            <p>{meta.subtitle}</p>
          </div>
          <div className="pharma-flow-shell__context-grid">
            {contextChips.map((chip) => (
              <ContextChip
                key={chip.label}
                icon={chip.icon}
                label={chip.label}
                value={chip.value}
              />
            ))}
          </div>
        </section>
      )}

      <nav className="pharma-flow-shell__steps" aria-label="Navigator flow">
        <button
          type="button"
          className={`pharma-flow-shell__overview-pill${space === "landing" ? " is-active" : ""}`}
          onClick={() => navigateSpace("landing")}
        >
          Overview
        </button>
        {PRIMARY_FLOW.map((step) => (
          <FlowStep
            key={step.key}
            active={space === step.key}
            number={step.number}
            label={SPACE_META[step.key].navLabel}
            onClick={() => navigateSpace(step.key)}
          />
        ))}
      </nav>

      <main className="pharma-flow-shell__content">{children}</main>
    </div>
  );
}
