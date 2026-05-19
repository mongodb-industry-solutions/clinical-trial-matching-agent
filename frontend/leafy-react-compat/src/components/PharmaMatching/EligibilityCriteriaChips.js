import React from "react";

export default function EligibilityCriteriaChips({ breakdown, maxVisible = null, showOverflow = true }) {
  if (!Array.isArray(breakdown) || breakdown.length === 0) return null;
  const safeMaxVisible = Number(maxVisible);
  const shouldLimit = Number.isFinite(safeMaxVisible) && safeMaxVisible > 0;
  const visibleRows = shouldLimit ? breakdown.slice(0, safeMaxVisible) : breakdown;
  const overflowCount = shouldLimit ? Math.max(0, breakdown.length - visibleRows.length) : 0;

  return (
    <div className="pharma-criteria-chips">
      {visibleRows.map((item, index) => {
        const rawStatus = item?.status ? String(item.status).toLowerCase() : null;
        const detailText = String(item?.detail || "").toLowerCase();
        const hasFail = Number(item?.failCount || 0) > 0 || rawStatus === "fail";
        const hasPass = Number(item?.passCount || 0) > 0 || rawStatus === "pass";
        const hasUnknown = Number(item?.unknownCount || 0) > 0 || detailText.includes("unknown") || rawStatus === "unknown";

        let status = rawStatus;
        if (!status) {
          if (hasFail && (hasPass || hasUnknown)) status = "mixed";
          else if (hasFail) status = "fail";
          else if (hasPass) status = "pass";
          else status = "unknown";
        }
        const chipStatus = status === "unknown" ? "na" : (status === "mixed" ? "warn" : status);
        const label = item?.criterion || item?.ruleId || item?.text || `rule-${index + 1}`;
        const passCount = Number(item?.passCount);
        const failCount = Number(item?.failCount);
        const unknownCount = Number(item?.unknownCount);
        const hasCounts = Number.isFinite(passCount) || Number.isFinite(failCount) || Number.isFinite(unknownCount);
        const passValue = Number.isFinite(passCount) ? passCount : 0;
        const failValue = Number.isFinite(failCount) ? failCount : 0;
        const unknownValue = Number.isFinite(unknownCount) ? unknownCount : 0;

        return (
          <span
            key={`${label}-${index}`}
            className={`pharma-criterion-chip ${chipStatus}`}
            title={item?.detail || item?.text || ""}
          >
            {label}
            {hasCounts ? (
              <span className="chip-metrics">{`P${passValue} F${failValue} U${unknownValue}`}</span>
            ) : null}
          </span>
        );
      })}
      {showOverflow && overflowCount > 0 ? (
        <span className="pharma-criterion-chip na overflow">+{overflowCount} more</span>
      ) : null}
    </div>
  );
}
