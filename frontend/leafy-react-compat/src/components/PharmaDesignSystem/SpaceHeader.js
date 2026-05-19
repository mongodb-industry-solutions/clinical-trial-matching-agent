import React from "react";

function SpaceHeader({ title, subtitle, onBack, backLabel = "Back" }) {
  return (
    <div className="pds-space-header">
      {onBack && (
        <button
          className="pds-space-header__back"
          onClick={onBack}
          type="button"
        >
          <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="12,4 6,10 12,16" />
          </svg>
          <span>{backLabel}</span>
        </button>
      )}
      <div className="pds-space-header__text">
        <h1 className="pds-space-header__title">{title}</h1>
        {subtitle && <p className="pds-space-header__subtitle">{subtitle}</p>}
      </div>
    </div>
  );
}

export default SpaceHeader;
