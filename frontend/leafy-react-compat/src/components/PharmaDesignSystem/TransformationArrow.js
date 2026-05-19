import React from "react";

const DEFAULT_STEPS = [
  { id: "source", label: "Source" },
  { id: "canonical", label: "Canonical Logic" },
  { id: "runtime", label: "Runtime" },
];

function TransformationArrow({ steps, compact }) {
  const resolvedSteps = steps && steps.length > 0 ? steps : DEFAULT_STEPS;

  return (
    <div className={`pds-transform-flow ${compact ? "pds-transform-flow--compact" : ""}`.trim()}>
      {resolvedSteps.map((step, i) => (
        <React.Fragment key={step.id}>
          <div
            className={`pds-transform-step pds-transform-step--${step.id} ${step.active ? "pds-transform-step--active" : ""}`.trim()}
          >
            {step.label}
          </div>
          {i < resolvedSteps.length - 1 && (
            <span className="pds-transform-arrow">&rarr;</span>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

export default TransformationArrow;
