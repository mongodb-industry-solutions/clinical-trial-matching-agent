import React from "react";

function FunnelVisualization({ stages, breakdown }) {
  if (!stages || stages.length === 0) return null;

  return (
    <div className="pds-funnel">
      <div className="pds-funnel__stages">
        {stages.map((stage, i) => (
          <React.Fragment key={i}>
            <div className="pds-funnel__stage">
              <div className="pds-funnel__stage-count">
                {stage.filterLabel ? (
                  <span className="pds-funnel__filter">{stage.filterLabel}</span>
                ) : (
                  stage.count
                )}
              </div>
              <div className="pds-funnel__stage-label">{stage.label}</div>
            </div>
            {i < stages.length - 1 && (
              <div className="pds-funnel__arrow">
                <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="9,6 15,12 9,18" />
                </svg>
              </div>
            )}
          </React.Fragment>
        ))}
      </div>

      {breakdown && breakdown.length > 0 && (
        <div className="pds-funnel__breakdown">
          {breakdown.map((item, i) => (
            <span key={i} className="pds-funnel__breakdown-chip">
              <span className="pds-funnel__breakdown-count">{item.count}</span>
              <span className="pds-funnel__breakdown-label">{item.label}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default FunnelVisualization;
