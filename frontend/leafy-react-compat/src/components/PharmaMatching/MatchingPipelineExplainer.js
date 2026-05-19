import React, { useEffect, useMemo, useRef, useState } from "react";

const FALLBACK_PATIENT_STEPS = [
  "Build patient context",
  "Filter the trial catalog",
  "Use retrieval support when helpful",
  "Check each criterion one by one",
  "Rank trial matches",
];

const FALLBACK_CANDIDATE_STEPS = [
  "Start from the selected trial",
  "Shortlist candidate patients",
  "Use retrieval support when helpful",
  "Check each criterion one by one",
  "Rank candidate patients",
];

const normalizeLiveSteps = (steps) => {
  if (!Array.isArray(steps)) return [];
  return steps
    .map((step) => String(step || "").trim())
    .filter(Boolean);
};

export default function MatchingPipelineExplainer({
  pipelineVisibility,
  mode = "patient",
  active = false,
  hasRun = false,
  autoScrollOnActive = false,
}) {
  const pv = pipelineVisibility || {};
  const candidateSearch = pv.candidateSearch || null;
  const isCandidateMode = mode === "candidate" || Boolean(candidateSearch);
  const runtime = isCandidateMode ? (candidateSearch || {}) : pv;
  const contextStatus = pv.patientContextStatus || runtime.patientContextStatus || null;
  const trialPrefilter = pv.trialPrefilter || runtime.trialPrefilter || null;
  const vectorEnabled = Boolean(
    isCandidateMode
      ? runtime.vectorEnabled
      : pv.hybridSearchEnabled
  );
  const vectorUsed = Boolean(
    isCandidateMode
      ? runtime.vectorUsed
      : pv.hybridSearchEnabled
  );
  const vectorFallbackReason = runtime.vectorFallbackReason || pv.vectorFallbackReason || "";
  const ruleEngine = pv.structuredRuleEngine || runtime.structuredRuleEngine || {};
  const countValue = trialPrefilter?.prefilteredTrialCount ?? trialPrefilter?.prefilteredCandidateCount ?? null;
  const hasPrefilter = Boolean(trialPrefilter);
  const steps = useMemo(() => {
    const stepRows = [];

    if (!isCandidateMode) {
      stepRows.push({
        key: "context",
        className: "context",
        label: "Build patient context",
        desc: contextStatus?.message || "Use the screening episode, narrative report, and linked FHIR facts for this patient.",
      });
    } else {
      stepRows.push({
        key: "contract",
        className: "context",
        label: "Start from the selected trial",
        desc: "Use the canonical trial contract to decide which cohort filters and criteria matter.",
      });
    }

    stepRows.push({
      key: "retrieval",
      className: "retrieval",
      label: isCandidateMode ? "Shortlist candidate patients" : "Shortlist candidate trials",
      desc: vectorUsed
        ? `Atlas vector search helped shortlist ${isCandidateMode ? "patients" : "trials"}${vectorFallbackReason ? ", with structured fallback available" : ""}.`
        : vectorEnabled
          ? `Structured retrieval took over for this ${isCandidateMode ? "patient" : "trial"} shortlist.`
          : `This run used deterministic structured retrieval only for the ${isCandidateMode ? "patient" : "trial"} shortlist.`,
    });

    stepRows.push({
      key: "prefilter",
      className: "prefilter",
      label: isCandidateMode ? "Apply hard cohort filters" : "Apply hard trial filters",
      desc: !hasPrefilter
        ? "No additional hard filters were required for this run."
        : countValue != null
        ? `${countValue} ${isCandidateMode ? "patients" : "trials"} remain after deterministic hard filters.`
        : "Apply deterministic hard filters before deeper review.",
    });

    stepRows.push({
      key: "rules",
      className: "review",
      label: "Check each criterion one by one",
      desc: ruleEngine?.uses?.length
        ? `${ruleEngine.uses.join(", ")} drive rule-by-rule adjudication against patient facts.`
        : "Evaluate inclusion and exclusion criteria one by one against patient evidence.",
    });

    stepRows.push({
      key: "rank",
      className: "rank",
      label: isCandidateMode ? "Rank candidate patients" : "Rank trial matches",
      desc: isCandidateMode
        ? "Use rule outcomes and evidence completeness to produce the shortlist."
        : "Use rule outcomes and evidence completeness to rank the matched trials.",
    });

    return stepRows;
  }, [
    countValue,
    contextStatus?.message,
    hasPrefilter,
    isCandidateMode,
    ruleEngine?.uses,
    vectorEnabled,
    vectorFallbackReason,
    vectorUsed,
  ]);

  const liveStepRows = useMemo(
    () =>
      normalizeLiveSteps(
        steps.map((step) => step.label).length
          ? steps.map((step) => step.label)
          : (isCandidateMode ? FALLBACK_CANDIDATE_STEPS : FALLBACK_PATIENT_STEPS)
      ),
    [isCandidateMode, steps]
  );
  const [liveStepIndex, setLiveStepIndex] = useState(0);
  const [liveFrame, setLiveFrame] = useState(1);
  const wasActiveRef = useRef(false);
  const stepSignatureRef = useRef("");
  const pipelineBottomRef = useRef(null);
  const stepSignature = liveStepRows.join("|");

  useEffect(() => {
    const wasActive = wasActiveRef.current;
    const signatureChanged = stepSignatureRef.current !== stepSignature;

    if (active && (!wasActive || signatureChanged)) {
      setLiveStepIndex(0);
      setLiveFrame(1);
    } else if (!active && wasActive) {
      setLiveStepIndex(hasRun ? Math.max(liveStepRows.length - 1, 0) : 0);
      setLiveFrame(1);
    } else if (!active && signatureChanged) {
      setLiveStepIndex(hasRun ? Math.max(liveStepRows.length - 1, 0) : 0);
    }

    wasActiveRef.current = active;
    stepSignatureRef.current = stepSignature;
    return undefined;
  }, [active, hasRun, liveStepRows.length, stepSignature]);

  useEffect(() => {
    if (!active) return undefined;
    const frameTimer = setInterval(() => {
      setLiveFrame((current) => (current >= 3 ? 1 : current + 1));
    }, 380);
    const stepTimer = setInterval(() => {
      setLiveStepIndex((current) => {
        if (liveStepRows.length === 0) return 0;
        return current >= liveStepRows.length - 1 ? current : current + 1;
      });
    }, 2300);
    return () => {
      clearInterval(frameTimer);
      clearInterval(stepTimer);
    };
  }, [active, liveStepRows.length]);

  useEffect(() => {
    if (!autoScrollOnActive || !active) return undefined;
    const frameId = window.requestAnimationFrame(() => {
      pipelineBottomRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "end",
        inline: "nearest",
      });
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [active, autoScrollOnActive, liveStepIndex]);

  const totalSteps = steps.length;
  const activeStep = steps[Math.min(liveStepIndex, Math.max(steps.length - 1, 0))] || null;
  const currentStepNumber = active
    ? Math.min(liveStepIndex + 1, Math.max(totalSteps, 1))
    : (hasRun ? totalSteps : 0);
  const pendingSteps = active
    ? steps.slice(liveStepIndex + 1)
    : (hasRun ? [] : steps);
  const statusLabel = active
    ? `Live · Step ${currentStepNumber} of ${Math.max(totalSteps, 1)}`
    : (hasRun ? "Completed" : "Ready to run");
  const statusCopy = active
    ? `${activeStep?.label || "Preparing matching"}${".".repeat(liveFrame)}`
    : hasRun
      ? "This run completed the full matching flow."
      : "Load a patient and define the trial scope, then start the matching run.";
  const progressPct = totalSteps > 0
    ? (active ? ((liveStepIndex + 1) / totalSteps) * 100 : (hasRun ? 100 : 0))
    : 0;
  const scopeSummary = (() => {
    if (countValue != null) {
      return `${countValue} ${isCandidateMode ? "patients" : "trials"} in deterministic scope`;
    }
    return isCandidateMode ? "One selected trial driving cohort screening" : "One selected patient driving trial matching";
  })();
  const signalChips = [
    scopeSummary,
    vectorUsed ? "retrieval support active" : (vectorEnabled ? "structured retrieval fallback" : "deterministic retrieval only"),
    ruleEngine?.uses?.length ? `${ruleEngine.uses.length} rule source${ruleEngine.uses.length === 1 ? "" : "s"}` : "criterion-by-criterion evaluation",
    hasRun && !active ? "decision trace ready" : null,
  ].filter(Boolean);

  return (
    <div className={`pharma-pipeline-explainer ${active ? "live" : ""} ${hasRun ? "complete" : "idle"}`.trim()}>
      <div className="pharma-pipeline-head">
        <div className="pharma-pipeline-head-copy">
          <strong>{isCandidateMode ? "Candidate matching flow" : "Matching flow"}</strong>
          <span className="pharma-pipeline-head-meta">{statusLabel}</span>
        </div>
        <span className={`pharma-pipeline-state-badge ${active ? "live" : (hasRun ? "complete" : "idle")}`}>
          {active ? "Running" : (hasRun ? "Done" : "Ready")}
        </span>
      </div>

      <p className="pharma-pipeline-state-copy">{statusCopy}</p>
      <div className="pharma-pipeline-signal-row">
        {signalChips.map((item) => (
          <span key={item} className="pharma-pipeline-signal-chip">{item}</span>
        ))}
      </div>
      {activeStep?.desc ? (
        <div className="pharma-pipeline-current-detail">
          <span className="pharma-pipeline-current-label">Current stage</span>
          <span>{activeStep.desc}</span>
        </div>
      ) : null}

      {totalSteps > 0 ? (
        <div className="pharma-pipeline-progress-bar" aria-hidden="true">
          <span className="pharma-pipeline-progress-fill" style={{ width: `${progressPct}%` }} />
        </div>
      ) : null}

      <div className="pharma-pipeline-steps">
        {steps.map((step, index) => {
          const stateClass = active
            ? (index < liveStepIndex ? "done" : (index === liveStepIndex ? "active" : "pending"))
            : (hasRun ? "done" : "pending");
          return (
            <div key={step.key} className={`pharma-pipeline-step ${step.className} ${stateClass}`.trim()}>
              <span className="pharma-pipeline-step-num">{index + 1}</span>
              <div className="pharma-pipeline-step-body">
                <span className="pharma-pipeline-step-label">{step.label}</span>
                <span className={`pharma-pipeline-step-state ${stateClass}`}>
                  {stateClass === "done" ? "Done" : stateClass === "active" ? "Running" : "Pending"}
                </span>
                <div className="pharma-pipeline-step-desc">{step.desc}</div>
              </div>
            </div>
          );
        })}
      </div>

      {pendingSteps.length > 0 ? (
        <div className="pharma-pipeline-pending">
          <strong>Still pending</strong>
          <div className="pharma-pipeline-pending-list">
            {pendingSteps.map((step) => (
              <span key={step.key} className="pharma-pipeline-pending-chip">
                {step.label}
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {vectorFallbackReason ? (
        <div className="pharma-pipeline-note">
          Structured fallback was available for this run.
        </div>
      ) : null}
      <div ref={pipelineBottomRef} />
    </div>
  );
}
