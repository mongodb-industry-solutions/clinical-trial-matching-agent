import React from "react";

function ThreePanelLayout({
  left,
  center,
  right,
  leftHeader,
  centerHeader,
  rightHeader,
  leftHeaderTone,
  centerHeaderTone,
  rightHeaderTone,
}) {
  const panels = [
    { content: left, header: leftHeader, tone: leftHeaderTone },
    { content: center, header: centerHeader, tone: centerHeaderTone },
    { content: right, header: rightHeader, tone: rightHeaderTone },
  ];

  return (
    <div className="pds-three-panel">
      {panels.map((panel, i) => (
        <div key={i} className="pds-three-panel__column">
          {panel.header && (
            <div
              className={`pds-three-panel__header ${panel.tone ? `pds-three-panel__header--${panel.tone}` : ""}`.trim()}
            >
              {panel.header}
            </div>
          )}
          <div className="pds-three-panel__content">{panel.content}</div>
        </div>
      ))}
    </div>
  );
}

export default ThreePanelLayout;
