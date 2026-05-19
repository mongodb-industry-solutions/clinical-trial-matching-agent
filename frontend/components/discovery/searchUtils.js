"use client";

import React from "react";

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function tokenizeSearchQuery(query) {
  return Array.from(
    new Set(
      String(query || "")
        .trim()
        .split(/\s+/)
        .map((token) => token.trim())
        .filter((token) => token.length > 1)
    )
  );
}

export function buildHighlightParts(text, query) {
  const source = String(text || "");
  const tokens = tokenizeSearchQuery(query).sort((left, right) => right.length - left.length);
  if (!source || tokens.length === 0) {
    return [{ text: source, highlight: false }];
  }

  const regex = new RegExp(`(${tokens.map(escapeRegExp).join("|")})`, "ig");
  const parts = [];
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(source)) !== null) {
    const start = match.index;
    const end = start + match[0].length;
    if (start > lastIndex) {
      parts.push({ text: source.slice(lastIndex, start), highlight: false });
    }
    parts.push({ text: source.slice(start, end), highlight: true });
    lastIndex = end;
  }

  if (lastIndex < source.length) {
    parts.push({ text: source.slice(lastIndex), highlight: false });
  }

  return parts.length > 0 ? parts : [{ text: source, highlight: false }];
}

export function renderHighlightedText(text, query, className = "mdb-inline-highlight") {
  return buildHighlightParts(text, query).map((part, index) =>
    part.highlight ? (
      <mark key={`${String(text)}-${index}`} className={className}>
        {part.text}
      </mark>
    ) : (
      <React.Fragment key={`${String(text)}-${index}`}>{part.text}</React.Fragment>
    )
  );
}

function scoreTextMatch(text, query) {
  const haystack = String(text || "").toLowerCase();
  const tokens = tokenizeSearchQuery(query).map((token) => token.toLowerCase());
  if (!haystack || tokens.length === 0) {
    return 0;
  }

  let score = 0;
  tokens.forEach((token) => {
    if (haystack.startsWith(token)) {
      score += 120;
    } else if (haystack.includes(` ${token}`)) {
      score += 90;
    } else if (haystack.includes(token)) {
      score += 60;
    }
  });
  return score;
}

export function buildConditionSuggestions(query, conditions = [], limit = 4) {
  const seen = new Set();

  return conditions
    .map((raw) => String(raw || "").trim())
    .filter(Boolean)
    .filter((label) => {
      const normalized = label.toLowerCase();
      if (seen.has(normalized)) {
        return false;
      }
      seen.add(normalized);
      return true;
    })
    .map((label) => ({
      id: `condition:${label.toLowerCase()}`,
      label,
      value: label,
      inputValue: label,
      secondary: "Condition search",
      kind: "Condition",
      submitMode: "condition",
      submitValue: label,
      score: scoreTextMatch(label, query),
    }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || left.label.length - right.label.length)
    .slice(0, limit);
}

export function buildTrialSuggestions(query, trials = [], limit = 6) {
  const seen = new Set();

  return trials
    .map((trial) => {
      const nctId = String(trial?.nct_id || trial?._id || "").trim();
      const title = String(trial?.brief_title || trial?.title || "").trim();
      const sponsor = String(trial?.sponsor || "").trim();
      const conditions = Array.isArray(trial?.conditions) ? trial.conditions.filter(Boolean) : [];
      const haystack = [nctId, title, sponsor, conditions.join(" ")].filter(Boolean).join(" ");
      const score =
        scoreTextMatch(nctId, query) * 2 +
        scoreTextMatch(title, query) * 3 +
        scoreTextMatch(sponsor, query) +
        scoreTextMatch(conditions.join(" "), query);

      return {
        id: `trial:${nctId || title}`,
        label: title || nctId || "Untitled trial",
        value: title || nctId || "",
        inputValue: title || nctId || "",
        secondary: [nctId, sponsor].filter(Boolean).join(" • "),
        kind: "Trial",
        submitMode: nctId ? "nct_id" : "search",
        submitValue: nctId || title,
        score,
        searchableText: haystack,
      };
    })
    .filter((item) => {
      const key = `${item.kind}:${item.submitValue}`.toLowerCase();
      if (!item.score || seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .sort((left, right) => right.score - left.score || left.label.length - right.label.length)
    .slice(0, limit);
}
