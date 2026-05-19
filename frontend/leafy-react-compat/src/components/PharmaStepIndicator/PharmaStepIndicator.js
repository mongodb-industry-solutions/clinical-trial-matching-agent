import React from 'react';

const steps = [
  { label: 'Data Sources' },
  { label: 'Trial Discovery' },
  { label: 'Patient Match' },
  { label: 'Cohort Intelligence' },
];

function PharmaStepIndicator({ currentStep, completedSteps, onStepClick }) {
  return (
    <div className="pharma-stepper">
      {steps.map((step, i) => {
        const stepNum = i + 1;
        const isCompleted = completedSteps.includes(stepNum) && currentStep !== stepNum;
        const isCurrent = currentStep === stepNum;
        const isClickable = isCompleted || isCurrent;
        return (
          <div className="pharma-stepper-item" key={stepNum}>
            <button
              type="button"
              className={[
                'pharma-stepper-circle',
                isCurrent ? 'current' : '',
                isCompleted ? 'completed' : '',
                !isClickable ? 'locked' : '',
              ].filter(Boolean).join(' ')}
              onClick={() => isClickable && onStepClick(stepNum)}
              disabled={!isClickable}
            >
              {isCompleted ? '\u2713' : stepNum}
            </button>
            <span
              className={[
                'pharma-stepper-label',
                isCurrent ? 'current' : '',
                isCompleted ? 'completed' : '',
              ].filter(Boolean).join(' ')}
            >
              {step.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default PharmaStepIndicator;
