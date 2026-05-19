"use client";

import { useState } from "react";

const FACET_DEFS = [
  { key: "phase",  label: "Phase" },
  { key: "status", label: "Status" },
  { key: "conditions", label: "Condition" },
  { key: "sponsor", label: "Sponsor" },
];

function FacetGroup({ title, items = [], activeValues = [], onToggle }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, 6);

  return (
    <div className="mdb-facet-group">
      <div className="mdb-facet-title">{title}</div>
      {visible.map((item) => {
        const active = activeValues.includes(item.value);
        return (
          <div
            key={item.value}
            className="mdb-facet-item"
            onClick={() => onToggle(item.value)}
            role="checkbox"
            aria-checked={active}
          >
            <span className={`mdb-facet-name ${active ? "active" : ""}`}>{item.label || item.value}</span>
            <span className="mdb-facet-count">{item.count?.toLocaleString() ?? ""}</span>
          </div>
        );
      })}
      {items.length > 6 && (
        <button className="mdb-facet-more-btn" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : `+${items.length - 6} more`}
        </button>
      )}
    </div>
  );
}

export default function FacetSidebar({ facets = {}, activeFilters = {}, onFilterToggle, onClearAll }) {
  const hasAny = Object.values(activeFilters).some((v) => v?.length > 0);

  return (
    <div className="mdb-facet-sidebar">
      <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--mdb-border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--mdb-slate)" }}>Filters</span>
        {hasAny && (
          <button className="mdb-clear-all" onClick={onClearAll}>Clear all</button>
        )}
      </div>
      {FACET_DEFS.map(({ key, label }) => {
        const items = buildFacetItems(facets, key);
        if (!items.length) return null;
        return (
          <FacetGroup
            key={key}
            title={label}
            items={items}
            activeValues={activeFilters[key] || []}
            onToggle={(value) => onFilterToggle(key, value)}
          />
        );
      })}
      {!FACET_DEFS.some(({ key }) => buildFacetItems(facets, key).length > 0) && (
        <div style={{ padding: "20px 16px", fontSize: 12, color: "var(--mdb-slate-30)", textAlign: "center" }}>
          Run a search to see filters
        </div>
      )}
    </div>
  );
}

function buildFacetItems(facets, key) {
  const raw = facets[key];
  if (!raw) return [];
  if (Array.isArray(raw)) {
    return raw.map((item) =>
      typeof item === "string"
        ? { value: item, label: item, count: null }
        : { value: item.value ?? item.name ?? String(item), label: item.label ?? item.name ?? String(item), count: item.count ?? null }
    );
  }
  return [];
}
