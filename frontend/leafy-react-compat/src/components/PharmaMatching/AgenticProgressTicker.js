import React, { useEffect, useMemo, useState } from "react";

const normalizeStepRows = (steps) => {
  if (!Array.isArray(steps)) return [];
  return steps
    .map((step) => String(step || "").trim())
    .filter((step) => step.length > 0);
};

export default function AgenticProgressTicker({
  active,
  title = "Agentic pipeline running",
  subtitle = "",
  steps = [],
}) {
  const stepRows = useMemo(() => normalizeStepRows(steps), [steps]);
  const [stepIndex, setStepIndex] = useState(0);
  const [dotFrame, setDotFrame] = useState(1);

  useEffect(() => {
    if (!active) {
      setStepIndex(0);
      setDotFrame(1);
      return undefined;
    }
    setStepIndex(0);
    setDotFrame(1);
    return undefined;
  }, [active, stepRows.length]);

  useEffect(() => {
    if (!active) {
      return undefined;
    }
    const dotTimer = setInterval(() => {
      setDotFrame((current) => (current >= 3 ? 1 : current + 1));
    }, 380);
    const stepTimer = setInterval(() => {
      setStepIndex((current) => {
        if (stepRows.length === 0) return 0;
        return current >= stepRows.length - 1 ? current : current + 1;
      });
    }, 1650);
    return () => {
      clearInterval(dotTimer);
      clearInterval(stepTimer);
    };
  }, [active, stepRows.length]);

  if (!active) return null;

  const currentStep = stepRows[stepIndex] || "Preparing candidate retrieval";
  const dots = ".".repeat(dotFrame);
  const stepNumber = stepRows.length > 0 ? Math.min(stepIndex + 1, stepRows.length) : 1;
  const pendingSteps = stepRows.slice(stepIndex + 1);

  return (
    <div className="pharma-agentic-progress">
      <div className="pharma-agentic-head">
        <div className="pharma-agentic-head-copy">
          <strong>{title}</strong>
          {stepRows.length > 0 ? (
            <span className="pharma-agentic-meta">
              Step {stepNumber} of {stepRows.length} · Pending {pendingSteps.length}
            </span>
          ) : null}
        </div>
        <span className="pharma-agentic-live">Live</span>
      </div>
      {subtitle ? <p>{subtitle}</p> : null}
      <div className="pharma-agentic-current">
        <span className="pharma-agentic-spinner" aria-hidden="true" />
        <span>{currentStep}<span className="pharma-agentic-dots">{dots}</span></span>
      </div>
      {stepRows.length > 0 ? (
        <div className="pharma-agentic-summary-bar" aria-hidden="true">
          <span
            className="pharma-agentic-summary-fill"
            style={{ width: `${(stepNumber / stepRows.length) * 100}%` }}
          />
        </div>
      ) : null}
      {stepRows.length > 0 ? (
        <div className="pharma-agentic-steps">
          {stepRows.map((step, idx) => {
            const status = idx < stepIndex ? "done" : (idx === stepIndex ? "active" : "pending");
            return (
              <div key={`${step}-${idx}`} className={`pharma-agentic-step ${status}`}>
                <span className="pharma-agentic-step-bullet" />
                <span>{step}</span>
              </div>
            );
          })}
        </div>
      ) : null}
      {pendingSteps.length > 0 ? (
        <div className="pharma-agentic-pending">
          <strong>Still pending</strong>
          <div className="pharma-agentic-pending-list">
            {pendingSteps.map((step) => (
              <span key={step} className="pharma-agentic-pending-chip">{step}</span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
