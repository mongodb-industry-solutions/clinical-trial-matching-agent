import React from "react";

function ReadinessScorecard({ metrics }) {
  if (!metrics || metrics.length === 0) return null;

  return (
    <div className="pds-scorecard">
      {metrics.map((metric, i) => (
        <div
          key={i}
          className={`pds-scorecard-item ${metric.tone ? `pds-scorecard-item--${metric.tone}` : ""}`.trim()}
        >
          <div className="pds-scorecard-item__label">{metric.label}</div>
          <div className="pds-scorecard-item__value">{metric.value}</div>
        </div>
      ))}
    </div>
  );
}

export default ReadinessScorecard;
