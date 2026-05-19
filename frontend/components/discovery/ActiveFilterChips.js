"use client";

const LABEL_MAP = {
  phase: "Phase",
  status: "Status",
  conditions: "Condition",
  sponsor: "Sponsor",
};

export default function ActiveFilterChips({ filters = {}, onRemove, onClearAll }) {
  const chips = Object.entries(filters).flatMap(([key, values]) =>
    (values || []).map((value) => ({ key, value }))
  );

  if (chips.length === 0) return null;

  return (
    <div className="mdb-chip-strip" style={{ marginBottom: 12 }}>
      {chips.map(({ key, value }) => (
        <span key={`${key}-${value}`} className="mdb-chip">
          <span className="mdb-chip-label">{LABEL_MAP[key] || key}</span>
          {value}
          <button
            className="mdb-chip-remove"
            onClick={() => onRemove(key, value)}
            aria-label={`Remove ${value}`}
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </span>
      ))}
      {chips.length >= 2 && (
        <button className="mdb-clear-all" onClick={onClearAll}>Clear all</button>
      )}
    </div>
  );
}
