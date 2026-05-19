import React from "react";

const scoreLabel = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "-";
  return numeric.toFixed(4);
};

export default function SemanticCriteriaMatches({ matches, maxRows = 8 }) {
  if (!Array.isArray(matches) || matches.length === 0) return null;

  return (
    <div className="pharma-semantic-matches">
      <strong>Semantic Criteria Matches</strong>
      <ul className="pharma-semantic-match-list">
        {matches.slice(0, maxRows).map((item, index) => (
          <li key={`${item?.ruleId || "rule"}-${index}`}>
            <span className="score">{scoreLabel(item?.score)}</span>
            <span className="text">{item?.text || item?.criterion || "-"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
