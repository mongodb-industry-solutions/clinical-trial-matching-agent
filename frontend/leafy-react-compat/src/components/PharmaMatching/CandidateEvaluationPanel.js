import React from "react";
import EligibilityDecisionSummary from "./EligibilityDecisionSummary";
import MatchReasonHighlights from "./MatchReasonHighlights";
import RuleEvaluationTable from "./RuleEvaluationTable";

const fmt = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(4) : "-";
};

const formatAssessmentLabel = (value) => {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "likely_eligible") return "Likely eligible";
  if (normalized === "likely_ineligible") return "Likely ineligible";
  return "Needs review";
};

export default function CandidateEvaluationPanel({ row, expanded = false }) {
  if (!row || typeof row !== "object") return null;
  const hasRules = Array.isArray(row.ruleEvaluations) && row.ruleEvaluations.length > 0;
  const hasSummary = row.decisionSummary || (Array.isArray(row.missingCriteria) && row.missingCriteria.length > 0);
  const parserWarnings = Array.isArray(row.parserWarnings) ? row.parserWarnings : [];
  const validatorWarnings = Array.isArray(row.validatorWarnings) ? row.validatorWarnings : [];
  const summary = row.decisionSummary && typeof row.decisionSummary === "object" ? row.decisionSummary : {};
  const unknown = Number(summary.unknownRules || 0);
  const pass = Number(summary.passRules || 0);
  const fail = Number(summary.failRules || 0);
  if (!hasRules && !hasSummary && parserWarnings.length === 0 && validatorWarnings.length === 0) return null;

  if (expanded) {
    return (
      <div className="pharma-candidate-explain-body expanded">
        <EligibilityDecisionSummary
          assessment={row.overallAssessment}
          decisionSummary={row.decisionSummary}
          missingCriteria={row.missingCriteria}
        />
        <MatchReasonHighlights rules={row.ruleEvaluations} maxItemsPerBucket={3} title="Why this candidate landed here" />
        {hasRules ? <RuleEvaluationTable rules={row.ruleEvaluations} maxRows={24} /> : null}
        {parserWarnings.length > 0 ? (
          <div className="pharma-candidate-warnings">
            Parser warnings: {parserWarnings.slice(0, 4).map((w) => w?.code || w).join(", ")}
          </div>
        ) : null}
        {validatorWarnings.length > 0 ? (
          <div className="pharma-candidate-warnings">
            Validator warnings: {validatorWarnings.slice(0, 4).map((w) => w?.code || w).join(", ")}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <details className="pharma-candidate-explain">
      <summary>
        <span className="pharma-candidate-summary-title">Why this candidate scored this way</span>
        <span>
          {formatAssessmentLabel(row.overallAssessment)} | Score {fmt(row.trialFitScore)}
          {` | Structured ${fmt(row.structuredScore)}`}
        </span>
        <span className="pharma-candidate-summary-metrics">
          PASS {pass} · FAIL {fail} · UNKNOWN {unknown}
        </span>
      </summary>
      <div className="pharma-candidate-explain-body">
        <EligibilityDecisionSummary
          assessment={row.overallAssessment}
          decisionSummary={row.decisionSummary}
          missingCriteria={row.missingCriteria}
          compact
        />
        <MatchReasonHighlights rules={row.ruleEvaluations} maxItemsPerBucket={2} title="At a glance" />
        {hasRules ? (
          <details className="pharma-candidate-deep-dive">
            <summary>Open rule-by-rule evidence</summary>
            <div className="pharma-candidate-deep-content">
              <RuleEvaluationTable rules={row.ruleEvaluations} maxRows={12} compact />
            </div>
          </details>
        ) : null}
        {parserWarnings.length > 0 ? (
          <div className="pharma-candidate-warnings">
            Parser warnings: {parserWarnings.slice(0, 4).map((w) => w?.code || w).join(", ")}
          </div>
        ) : null}
        {validatorWarnings.length > 0 ? (
          <div className="pharma-candidate-warnings">
            Validator warnings: {validatorWarnings.slice(0, 4).map((w) => w?.code || w).join(", ")}
          </div>
        ) : null}
      </div>
    </details>
  );
}
