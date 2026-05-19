import React from "react";
import StatusChip from "./StatusChip";
import CriterionStatusBar from "./CriterionStatusBar";

function ObjectCard({
  title,
  subtitle,
  avatar,
  chips,
  selected,
  onClick,
  statusBar,
  children,
}) {
  return (
    <div
      className={`pds-object-card ${selected ? "pds-object-card--selected" : ""}`.trim()}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick(e);
              }
            }
          : undefined
      }
    >
      {avatar && (
        <div className="pds-object-card__avatar">{avatar}</div>
      )}
      <div className="pds-object-card__body">
        <div className="pds-object-card__title">{title}</div>
        {subtitle && <div className="pds-object-card__subtitle">{subtitle}</div>}

        {chips && chips.length > 0 && (
          <div className="pds-object-card__chips">
            {chips.map((chip, i) => (
              <StatusChip key={i} status={chip.status} label={chip.label} size="sm" />
            ))}
          </div>
        )}

        {statusBar && (
          <div className="pds-object-card__status-bar">
            <CriterionStatusBar counts={statusBar} showLegend={false} />
          </div>
        )}

        {children}
      </div>
    </div>
  );
}

export default ObjectCard;
