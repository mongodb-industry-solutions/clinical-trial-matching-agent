"use client";

import { useState, useEffect, useMemo, useRef } from "react";
import { renderHighlightedText } from "./searchUtils";

function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

function normalizeSuggestion(entry, index) {
  if (typeof entry === "string") {
    return {
      id: `suggestion:${entry}:${index}`,
      label: entry,
      value: entry,
      inputValue: entry,
      secondary: "",
      kind: "",
      submitValue: entry,
    };
  }

  return {
    id: entry?.id || `suggestion:${entry?.value || entry?.label || index}`,
    label: entry?.label || entry?.value || "",
    value: entry?.value || entry?.label || "",
    inputValue: entry?.inputValue || entry?.value || entry?.label || "",
    secondary: entry?.secondary || "",
    kind: entry?.kind || "",
    submitMode: entry?.submitMode || "search",
    submitValue: entry?.submitValue || entry?.value || entry?.label || "",
  };
}

export default function TrialSearchBar({
  value,
  onChange,
  onSearch,
  onSuggestionSelect,
  placeholder,
  autocompleteSuggestions = [],
  loading = false,
  emptyMessage = "No matches found in the current corpus",
  minChars = 2,
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const inputRef = useRef(null);
  const wrapRef = useRef(null);
  const debounced = useDebounce(value, 350);

  const visibleSuggestions = useMemo(
    () => autocompleteSuggestions.map(normalizeSuggestion).slice(0, 8),
    [autocompleteSuggestions]
  );

  useEffect(() => {
    if (debounced.length >= minChars && (loading || visibleSuggestions.length > 0)) setOpen(true);
    else setOpen(false);
  }, [debounced, loading, minChars, visibleSuggestions.length]);

  useEffect(() => {
    setActiveIndex(visibleSuggestions.length > 0 ? 0 : -1);
  }, [visibleSuggestions]);

  useEffect(() => {
    const handler = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleKeyDown = (e) => {
    if (e.key === "ArrowDown" && open && visibleSuggestions.length > 0) {
      e.preventDefault();
      setActiveIndex((current) => (current + 1) % visibleSuggestions.length);
      return;
    }
    if (e.key === "ArrowUp" && open && visibleSuggestions.length > 0) {
      e.preventDefault();
      setActiveIndex((current) => (current <= 0 ? visibleSuggestions.length - 1 : current - 1));
      return;
    }
    if (e.key === "Enter") {
      if (open && visibleSuggestions.length > 0) {
        e.preventDefault();
        const selected = visibleSuggestions[Math.max(activeIndex, 0)] || visibleSuggestions[0];
        handleSuggestionClick(selected);
        return;
      }
      setOpen(false);
      onSearch(value);
      return;
    }
    if (e.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  };

  const handleSuggestionClick = (suggestion) => {
    onChange(suggestion.inputValue || suggestion.value || "");
    setOpen(false);
    if (onSuggestionSelect) {
      onSuggestionSelect(suggestion);
      return;
    }
    onSearch(suggestion.submitValue || suggestion.value || suggestion.label || "");
  };

  return (
    <div ref={wrapRef} className="mdb-search-wrap" style={{ flex: 1 }}>
      <svg className="mdb-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
      </svg>
      <input
        ref={inputRef}
        type="text"
        className="mdb-search-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={() => value.length >= minChars && (loading || visibleSuggestions.length > 0) && setOpen(true)}
        placeholder={placeholder || "Search by condition, NCT ID, sponsor, or intervention…"}
        autoComplete="off"
        spellCheck={false}
      />
      {value && (
        <button
          className="mdb-search-clear"
          onClick={() => { onChange(""); onSearch(""); inputRef.current?.focus(); }}
          aria-label="Clear search"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      )}
      {open && (
        <div className="mdb-autocomplete">
          {loading ? (
            <div className="mdb-autocomplete-state">Searching the current corpus…</div>
          ) : visibleSuggestions.length > 0 ? (
            visibleSuggestions.map((suggestion, index) => (
              <div
                key={suggestion.id}
                className={`mdb-autocomplete-item${activeIndex === index ? " active" : ""}`}
                onMouseDown={() => handleSuggestionClick(suggestion)}
                onMouseEnter={() => setActiveIndex(index)}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0, opacity: 0.4 }}>
                  <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
                </svg>
                <div className="mdb-autocomplete-item-copy">
                  <div className="mdb-autocomplete-item-primary">
                    {suggestion.kind ? <span className="mdb-autocomplete-badge">{suggestion.kind}</span> : null}
                    <span>{renderHighlightedText(suggestion.label, value)}</span>
                  </div>
                  {suggestion.secondary ? (
                    <div className="mdb-autocomplete-item-secondary">
                      {renderHighlightedText(suggestion.secondary, value)}
                    </div>
                  ) : null}
                </div>
              </div>
            ))
          ) : (
            <div className="mdb-autocomplete-state">{emptyMessage}</div>
          )}
        </div>
      )}
    </div>
  );
}
