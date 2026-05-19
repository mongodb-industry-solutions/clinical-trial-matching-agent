import React from "react";

const labelize = (value) => String(value || "").replaceAll("_", " ").trim() || "-";

const truthyCount = (items, predicate) => {
  if (!Array.isArray(items)) return 0;
  return items.reduce((total, item) => (predicate(item) ? total + 1 : total), 0);
};

const renderFilterTokens = (filters) => {
  const hardFilters = (filters || {}).hardFilters || {};
  const tokens = [];

  if (hardFilters.sex?.anyOf?.length) {
    tokens.push(`sex: ${hardFilters.sex.anyOf.join(", ")}`);
  }
  if (hardFilters.age && (hardFilters.age.min != null || hardFilters.age.max != null)) {
    const ageBits = [];
    if (hardFilters.age.min != null) ageBits.push(`min ${hardFilters.age.min}`);
    if (hardFilters.age.max != null) ageBits.push(`max ${hardFilters.age.max}`);
    tokens.push(`age: ${ageBits.join(" / ")}`);
  }
  return tokens;
};

const metricTone = (value, fallback = "neutral") => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  if (numeric === 0) return "pass";
  return fallback;
};

export default function DeterministicEvidencePanel({ row, title = "How this trial is prepared for matching", compact = false }) {
  if (!row || typeof row !== "object") return null;

  const prefilterEvaluation = row.prefilterEvaluation || {};
  const prefilterContract = row.trialPrefilterContract || {};
  const canonicalDefinition = row.trialDefinitionCanonical || {};
  const canonicalSummary = canonicalDefinition.summary || row.trialDefinitionCanonicalSummary || {};
  const filterTokens = renderFilterTokens(prefilterContract);
  const blockedBy = Array.isArray(prefilterEvaluation.blockedBy) ? prefilterEvaluation.blockedBy : [];
  const missingData = Array.isArray(prefilterEvaluation.missingPatientData) ? prefilterEvaluation.missingPatientData : [];
  const advisoryMatches = Array.isArray(prefilterEvaluation.advisoryMatches) ? prefilterEvaluation.advisoryMatches : [];
  const supportedGroups = Array.isArray(prefilterContract.supportedCriterionGroups) ? prefilterContract.supportedCriterionGroups : [];
  const unsupportedGroups = Array.isArray(prefilterContract.unsupportedCriterionGroups) ? prefilterContract.unsupportedCriterionGroups : [];
  const hasContract = Object.keys(prefilterContract).length > 0;
  const hasPrefilter = Object.keys(prefilterEvaluation).length > 0;
  const hasCanonical = Object.keys(canonicalDefinition).length > 0 || Object.keys(canonicalSummary).length > 0;
  const canonicalCriteria = Array.isArray(canonicalDefinition.criteria) ? canonicalDefinition.criteria : [];
  const totalCriteria = Number(canonicalSummary.totalCriteria || 0);
  const deterministicCriteria = Number(canonicalSummary.deterministicCriteria || 0);
  const reviewCriteria = Math.max(0, totalCriteria - deterministicCriteria);
  const contractVersion = canonicalDefinition.contractVersion || prefilterContract.contractVersion || "trial_definition_canonical_v1";
  const hardFilterCount = prefilterContract.hardFilterCount ?? filterTokens.length;
  const rawCriteriaCount = Number(row.totalParsedRules || totalCriteria || canonicalCriteria.length || 0);
  const stableIdCount = truthyCount(canonicalCriteria, (item) => Boolean(item?.criterionId));
  const sdtmMappedCount = truthyCount(canonicalCriteria, (item) => Boolean(item?.cdisc?.sdtmTi?.ietestcd));
  const parserWarningCount = Array.isArray(row.parserWarnings) ? row.parserWarnings.length : Number(canonicalSummary.parserWarningCount || 0);
  const standards = canonicalDefinition.standards || {};
  const standardLabels = [
    standards.trialDefinition,
    standards.criterionIdentity,
    standards.criterionTabulation,
    standards.patientOutcome,
  ].filter(Boolean);
  const topMetrics = [
    {
      label: "Canonical criteria",
      value: totalCriteria || canonicalCriteria.length || "-",
      tone: "neutral",
      hint: "Typed rules stored in the governed matcher contract",
    },
    {
      label: "Deterministic now",
      value: deterministicCriteria || 0,
      tone: deterministicCriteria > 0 ? "pass" : "neutral",
      hint: "Criteria that can already run on structured patient evidence",
    },
    {
      label: "Still needs review",
      value: hasCanonical ? reviewCriteria : unsupportedGroups.length,
      tone: metricTone(hasCanonical ? reviewCriteria : unsupportedGroups.length, "review"),
      hint: "Criteria still requiring narrative interpretation or more ontology support",
    },
    {
      label: "Hard filters",
      value: hardFilterCount,
      tone: hardFilterCount > 0 ? "neutral" : "pass",
      hint: "Fast prefilter rules applied before deeper evaluation",
    },
  ];
  const transformationFacts = [
    { label: "Storage", value: "Canonical MongoDB contract" },
    { label: "Source criteria", value: rawCriteriaCount || "-" },
    { label: "Stable IDs", value: stableIdCount || "-" },
    { label: "SDTM mappings", value: sdtmMappedCount || "-" },
    { label: "Parser warnings", value: parserWarningCount || "-" },
  ];
  const qualityChips = [
    "stable criterion ids",
    "typed categories",
    "computability flags",
    hardFilterCount > 0 ? `${hardFilterCount} safe hard filters` : null,
    reviewCriteria > 0 ? `${reviewCriteria} still need support` : "all current groups typed",
  ].filter(Boolean);
  const exampleCriteria = canonicalCriteria
    .filter((item) => String(item?.criterionText || "").trim())
    .slice(0, compact ? 2 : 3);

  if (!hasContract && !hasPrefilter && !hasCanonical) return null;

  return (
    <div className={`pharma-deterministic-panel ${compact ? "compact" : ""}`}>
      <div className="pharma-deterministic-panel-head">
        <h4>{title}</h4>
        {hasPrefilter ? (
          <span className={`pharma-deterministic-status ${prefilterEvaluation.passesHardFilters ? "pass" : "blocked"}`}>
            {prefilterEvaluation.passesHardFilters ? "Hard filters passed" : "Blocked by hard filter"}
          </span>
        ) : null}
      </div>

      <div className="pharma-deterministic-contract">
        <div className="pharma-deterministic-flow">
          <span className="pharma-deterministic-flow-step">ClinicalTrials.gov free text</span>
          <span className="pharma-deterministic-flow-arrow">→</span>
          <span className="pharma-deterministic-flow-step">Canonical trial contract</span>
          <span className="pharma-deterministic-flow-arrow">→</span>
          <span className="pharma-deterministic-flow-step">Deterministic criterion checks</span>
        </div>
        <p className="pharma-deterministic-copy">
          Raw eligibility text is converted into a governed matcher contract before any patient is evaluated. That gives each rule a stable identity, a typed category, a computability state, and an explicit place in the final decision trace.
        </p>
        <div className="pharma-evidence-chip-row pharma-deterministic-quality-row">
          {qualityChips.map((item) => (
            <span key={item} className="pharma-evidence-chip neutral">{item}</span>
          ))}
        </div>
        <div className="pharma-deterministic-summary-grid">
          {topMetrics.map((metric) => (
            <div key={metric.label} className={`pharma-deterministic-stat-card ${metric.tone}`}>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <small>{metric.hint}</small>
            </div>
          ))}
        </div>
        <div className="pharma-deterministic-facts">
          <div className="pharma-deterministic-facts-head">
            <strong>Contract facts</strong>
            <span>{contractVersion}</span>
          </div>
          <div className="pharma-evidence-chip-row">
            {transformationFacts.map((metric) => (
              <span key={metric.label} className="pharma-evidence-chip neutral">
                <strong>{metric.label}:</strong> {metric.value}
              </span>
            ))}
          </div>
        </div>
        <div className="pharma-deterministic-section-grid">
          <div className="pharma-deterministic-section pass">
            <div className="pharma-deterministic-section-head">
              <strong>What can run deterministically now</strong>
              <span>Safe structured checks already supported</span>
            </div>
            {filterTokens.length > 0 ? (
              <div className="pharma-evidence-group">
                <span className="pharma-evidence-group-label">Hard filters in play</span>
                <div className="pharma-evidence-chip-row">
                  {filterTokens.map((token) => (
                    <span key={token} className="pharma-evidence-chip neutral">{token}</span>
                  ))}
                </div>
              </div>
            ) : null}
            {supportedGroups.length > 0 ? (
              <div className="pharma-evidence-group">
                <span className="pharma-evidence-group-label">Deterministic criterion groups</span>
                <div className="pharma-evidence-chip-row">
                  {supportedGroups.slice(0, 10).map((item) => (
                    <span key={item} className="pharma-evidence-chip pass">{labelize(item)}</span>
                  ))}
                </div>
              </div>
            ) : (
              <p className="pharma-deterministic-empty">This trial does not yet expose structured groups we can run deterministically.</p>
            )}
          </div>
          <div className="pharma-deterministic-section review">
            <div className="pharma-deterministic-section-head">
              <strong>What still needs support</strong>
              <span>Ontology depth, narrative interpretation, or more patient evidence</span>
            </div>
            {unsupportedGroups.length > 0 ? (
              <div className="pharma-evidence-group">
                <span className="pharma-evidence-group-label">Needs narrative or ontology support</span>
                <div className="pharma-evidence-chip-row">
                  {unsupportedGroups.slice(0, 10).map((item) => (
                    <span key={item} className="pharma-evidence-chip review">{labelize(item)}</span>
                  ))}
                </div>
              </div>
            ) : (
              <p className="pharma-deterministic-empty">No unsupported criterion groups are currently flagged for this contract.</p>
            )}
            {blockedBy.length > 0 ? (
              <div className="pharma-evidence-group">
                <span className="pharma-evidence-group-label">Blocked by</span>
                <div className="pharma-evidence-chip-row">
                  {blockedBy.map((item) => (
                    <span key={item} className="pharma-evidence-chip blocked">{labelize(item)}</span>
                  ))}
                </div>
              </div>
            ) : null}
            {missingData.length > 0 ? (
              <div className="pharma-evidence-group">
                <span className="pharma-evidence-group-label">Missing patient data</span>
                <div className="pharma-evidence-chip-row">
                  {missingData.map((item) => (
                    <span key={item} className="pharma-evidence-chip review">{labelize(item)}</span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {standardLabels.length > 0 || advisoryMatches.length > 0 ? (
        <div className="pharma-deterministic-section neutral">
          <div className="pharma-deterministic-section-head">
            <strong>Standards and supporting signals</strong>
            <span>What the transformed contract preserves from the original trial definition</span>
          </div>
          {standardLabels.length > 0 ? (
            <div className="pharma-evidence-group">
              <span className="pharma-evidence-group-label">CDISC alignment visible in this contract</span>
              <div className="pharma-evidence-chip-row">
                {standardLabels.map((item) => (
                  <span key={item} className="pharma-evidence-chip neutral">{item}</span>
                ))}
              </div>
            </div>
          ) : null}
          {advisoryMatches.length > 0 ? (
            <div className="pharma-evidence-group">
              <span className="pharma-evidence-group-label">Advisory matches</span>
              <div className="pharma-evidence-chip-row">
                {advisoryMatches.map((item) => (
                  <span key={item} className="pharma-evidence-chip pass">{labelize(item)}</span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {exampleCriteria.length > 0 ? (
        <details className="pharma-deterministic-examples" open={false}>
          <summary>See transformed criterion examples</summary>
          <div className="pharma-deterministic-example-list">
            {exampleCriteria.map((item) => (
              <div
                key={item.criterionId || item.ordinal}
                className={`pharma-deterministic-example-card ${item.computability === "deterministic" ? "pass" : "review"}`}
              >
                <div className="pharma-deterministic-example-head">
                  <strong>{item.criterionText}</strong>
                  {item.computability ? (
                    <span className={`pharma-evidence-chip ${item.computability === "deterministic" ? "pass" : "review"}`}>
                      {labelize(item.computability)}
                    </span>
                  ) : null}
                </div>
                <div className="pharma-evidence-chip-row">
                  {item.criterionId ? <span className="pharma-evidence-chip neutral">{item.criterionId}</span> : null}
                  {item.criterionType ? <span className="pharma-evidence-chip neutral">{labelize(item.criterionType)}</span> : null}
                  {item.criterionCategory ? <span className="pharma-evidence-chip pass">{labelize(item.criterionCategory)}</span> : null}
                  {item?.cdisc?.sdtmTi?.ietestcd ? (
                    <span className="pharma-evidence-chip neutral">SDTM {item.cdisc.sdtmTi.ietestcd}</span>
                  ) : null}
                  {item?.cdisc?.usdmDomain ? (
                    <span className="pharma-evidence-chip neutral">USDM {item.cdisc.usdmDomain}</span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
