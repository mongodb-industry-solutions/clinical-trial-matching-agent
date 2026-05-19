import React, { useEffect, useMemo, useState } from "react";
import {
  buildPatientClinicalRecord,
  classifyClinicalRecordEventType,
  CLINICAL_RECORD_EVENT_LABELS,
} from "./patientClinicalRecordModel";

const formatDateTime = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "-";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleString();
};

const formatDateShort = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "Undated";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
};

const compactText = (value, maxLength = 480) => {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength).trim()}...`;
};

const renderDomainItemLabel = (item) => {
  if (!item || typeof item !== "object") return "-";
  return item.name || item.title || item.label || item.value || item.status || "-";
};

const renderDomainItemMeta = (item) => {
  if (!item || typeof item !== "object") return "";
  return [
    item.status,
    item.value,
    item.intent,
    item.clinicalStatus,
    item.verificationStatus,
    item.authoredOn ? formatDateTime(item.authoredOn) : "",
    item.effectiveDateTime ? formatDateTime(item.effectiveDateTime) : "",
    item.onsetDateTime ? formatDateTime(item.onsetDateTime) : "",
    item.period?.start ? formatDateTime(item.period.start) : "",
  ].filter(Boolean).join(" · ");
};

const domainTitle = (key) => CLINICAL_RECORD_EVENT_LABELS[key] || String(key || "Domain").replace(/[_-]+/g, " ");

const getTimelineSummaryCards = ({ emr, identity, screening, structuredHighlights }) => {
  const totalMatched = Number(emr?.summary?.totalMatchedResources || emr?.resources?.length || 0);
  const timelineEvents = Number(emr?.summary?.timelineEvents || emr?.timeline?.length || 0);
  return [
    { label: "Patient record", value: identity.syntheaPatientRef ? "Linked" : "Pending" },
    { label: "Timeline events", value: timelineEvents || 0 },
    { label: "Clinical records", value: totalMatched || 0 },
    { label: "Age / sex", value: [identity.age ?? "-", identity.sex || "-"].join(" · ") },
    { label: "Screening views", value: screening.numberImages ?? 0 },
    { label: "Cancer facts", value: structuredHighlights.length || 0 },
  ];
};

export default function PatientClinicalRecord({
  record,
  patientContext,
  patientContextStatus,
  evidencePack,
  reportDoc,
  appointmentDoc,
  patientLink,
  emrPayload,
  isLoading = false,
  className = "",
}) {
  const resolvedRecord = useMemo(() => (
    record || buildPatientClinicalRecord({
      patientContext,
      patientContextStatus,
      evidencePack,
      reportDoc,
      appointmentDoc,
      patientLink,
      emrPayload,
    })
  ), [record, patientContext, patientContextStatus, evidencePack, reportDoc, appointmentDoc, patientLink, emrPayload]);

  const [activeFilter, setActiveFilter] = useState(null);
  const [activePanel, setActivePanel] = useState("timeline");

  const timeline = useMemo(() => (
    Array.isArray(resolvedRecord?.emr?.timeline) ? resolvedRecord.emr.timeline : []
  ), [resolvedRecord]);

  const classifiedEvents = useMemo(() => (
    timeline.map((event) => ({
      ...event,
      _type: classifyClinicalRecordEventType(event?.resourceType),
      _dateKey: formatDateShort(event?.date),
    }))
  ), [timeline]);

  const typeCounts = useMemo(() => {
    const counts = {};
    classifiedEvents.forEach((event) => {
      counts[event._type] = (counts[event._type] || 0) + 1;
    });
    return counts;
  }, [classifiedEvents]);

  const filteredEvents = useMemo(() => {
    if (!activeFilter) return classifiedEvents;
    return classifiedEvents.filter((event) => event._type === activeFilter);
  }, [classifiedEvents, activeFilter]);

  const dateGroups = useMemo(() => {
    const groups = new Map();
    filteredEvents.forEach((event) => {
      const key = event._dateKey;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(event);
    });
    return Array.from(groups.entries());
  }, [filteredEvents]);

  useEffect(() => {
    setActiveFilter(null);
  }, [resolvedRecord?.identity?.syntheaPatientRef, resolvedRecord?.identity?.appointmentId]);
  useEffect(() => {
    setActivePanel("timeline");
  }, [resolvedRecord?.identity?.syntheaPatientRef, resolvedRecord?.identity?.appointmentId]);

  const screening = resolvedRecord?.screening || {};
  const emr = resolvedRecord?.emr || {};
  const emrError = emrPayload?.success === false ? String(emrPayload?.error || "") : "";
  const identity = resolvedRecord?.identity || {};
  const structuredHighlights = Array.isArray(resolvedRecord?.evidence?.structuredHighlights)
    ? resolvedRecord.evidence.structuredHighlights
    : [];
  const resourceTypeCounts = emr?.summary?.resourceTypeCounts || {};
  const domainEntries = Object.entries(emr?.domains || {}).filter(([, rows]) => Array.isArray(rows) && rows.length > 0);
  const screeningNarratives = [
    screening.clinicalNotes ? { title: "Clinical notes", body: screening.clinicalNotes } : null,
    screening.conclusions ? { title: "Conclusions", body: screening.conclusions } : null,
  ].filter(Boolean);
  const summaryCards = getTimelineSummaryCards({ emr, identity, screening, structuredHighlights });
  const hasInconsistentLink = Boolean(
    identity.syntheaPatientRef
    && !isLoading
    && !emrError
    && !emr.resources?.length
    && !classifiedEvents.length
  );
  const availablePanels = [
    { key: "timeline", label: "Timeline" },
    { key: "domains", label: "Clinical domains", disabled: domainEntries.length === 0 },
    screening.present ? { key: "screening", label: "Leafy screening" } : null,
  ].filter(Boolean);

  return (
    <section className={`patient-record-shell ${className}`.trim()}>
      <div className="patient-record-head patient-record-head-compact">
        <div>
          <span className="patient-record-eyebrow">Patient EHR</span>
          <h3>{identity.anonymousLabel || identity.displayName || "Selected patient"}</h3>
          <p>
            Patient EMR first, with current screening context added when available.
          </p>
        </div>
        <span className={`patient-record-status ${resolvedRecord?.status?.tone || "neutral"}`}>
          {resolvedRecord?.status?.label || "Context loaded"}
        </span>
      </div>

      <div className="patient-record-meta-row">
        <span className="patient-record-meta-chip">
          {identity.syntheaPatientRef ? "Linked patient record" : "Patient record pending"}
        </span>
        {screening.present ? (
          <span className="patient-record-meta-chip">
            Leafy screening attached
            {screening.reportDate ? ` · ${formatDateShort(screening.reportDate)}` : ""}
          </span>
        ) : null}
      </div>

      <div className="patient-record-summary-grid">
        {summaryCards.map((item) => (
          <div key={item.label} className="patient-record-summary-card">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>

      <div className="patient-record-view-tabs" role="tablist" aria-label="Patient record views">
        {availablePanels.map((panel) => (
          <button
            key={panel.key}
            type="button"
            role="tab"
            aria-selected={activePanel === panel.key}
            className={`patient-record-view-tab ${activePanel === panel.key ? "active" : ""}`}
            onClick={() => {
              if (!panel.disabled) setActivePanel(panel.key);
            }}
            disabled={panel.disabled}
          >
            {panel.label}
          </button>
        ))}
      </div>

      {activePanel === "timeline" ? (
      <div className="patient-record-section patient-record-section-primary">
        <div className="patient-record-section-head">
          <div>
            <h4>Patient EMR timeline</h4>
            <p>Start with dated clinical events. Open grouped views only when you need deeper detail.</p>
          </div>
          <div className="patient-record-section-note">
            {isLoading ? "Loading patient record..." : `${Number(emr?.summary?.selectedResources || emr?.resources?.length || 0)} records loaded`}
          </div>
        </div>

        <div className="patient-record-chip-row">
          <span className="patient-record-chip neutral">
            Matched {Number(emr?.summary?.totalMatchedResources || emr?.resources?.length || 0)} resource
            {Number(emr?.summary?.totalMatchedResources || emr?.resources?.length || 0) === 1 ? "" : "s"}
          </span>
          {structuredHighlights.map((item) => (
            <span key={item.key} className="patient-record-chip success">
              <strong>{item.label}:</strong> {item.value}
            </span>
          ))}
          {Object.entries(resourceTypeCounts).map(([resourceType, count]) => (
            <span key={resourceType} className="patient-record-chip neutral">
              {resourceType} {count}
            </span>
          ))}
        </div>

        <div className="patient-record-filter-bar">
          <button
            type="button"
            className={`patient-record-filter-chip ${!activeFilter ? "active" : ""}`}
            onClick={() => setActiveFilter(null)}
          >
            All <span>{classifiedEvents.length}</span>
          </button>
          {Object.entries(typeCounts).map(([type, count]) => (
            <button
              key={type}
              type="button"
              className={`patient-record-filter-chip patient-record-filter-${type} ${activeFilter === type ? "active" : ""}`}
              onClick={() => setActiveFilter((prev) => (prev === type ? null : type))}
            >
              {CLINICAL_RECORD_EVENT_LABELS[type] || type} <span>{count}</span>
            </button>
          ))}
        </div>

        {emrError ? (
          <div className="patient-record-empty">{emrError}</div>
        ) : isLoading ? (
          <div className="patient-record-loading">
            <span className="patient-record-spinner" aria-hidden="true" />
            <span>Loading patient timeline...</span>
          </div>
        ) : dateGroups.length > 0 ? (
          <div className="patient-record-date-groups">
            {dateGroups.map(([dateKey, events], index) => (
              <details key={`${dateKey}-${index}`} className="patient-record-date-group" open={index < 6}>
                <summary>
                  <span>{dateKey}</span>
                  <span>{events.length} event{events.length === 1 ? "" : "s"}</span>
                </summary>
                <div className="patient-record-date-events">
                  {events.map((event, eventIndex) => (
                    <article
                      key={`${dateKey}-${eventIndex}-${event.reference || event.title}`}
                      className={`patient-record-event patient-record-event-${event._type}`}
                    >
                      <div className="patient-record-event-rail" aria-hidden="true" />
                      <div className={`patient-record-event-type patient-record-event-type-${event._type}`}>
                        {event.resourceType || "Event"}
                      </div>
                      <div className="patient-record-event-body">
                        <strong>{event.title || "-"}</strong>
                        <p>
                          {[event.value, event.status, event.reference].filter(Boolean).join(" · ") || "No additional detail"}
                        </p>
                      </div>
                    </article>
                  ))}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <div className={`patient-record-empty ${hasInconsistentLink ? "patient-record-empty-warning" : ""}`}>
            {hasInconsistentLink
              ? "A linked patient record was resolved, but no longitudinal events were returned. Verify the linked longitudinal patient reference."
              : "No longitudinal EMR events are available for this patient yet."}
          </div>
        )}
      </div>
      ) : null}

      {activePanel === "domains" && domainEntries.length > 0 ? (
        <div className="patient-record-section">
          <div className="patient-record-section-head">
            <div>
              <h4>Clinical domains</h4>
              <p>Inspect grouped summaries for medications, labs, conditions, encounters, and procedures.</p>
            </div>
          </div>
          <div className="patient-record-domain-grid">
            {domainEntries.map(([key, rows]) => (
              <details key={key} className="patient-record-domain">
                <summary>{domainTitle(key)} ({rows.length})</summary>
                <div className="patient-record-domain-list">
                  {rows.map((item, index) => (
                    <div key={`${key}-${index}`} className="patient-record-domain-item">
                      <strong>{renderDomainItemLabel(item)}</strong>
                      <span>{renderDomainItemMeta(item) || "No additional metadata"}</span>
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </div>
      ) : null}

      {activePanel === "screening" && screening.present ? (
        <div className="patient-record-section">
          <div className="patient-record-section-head">
            <div>
              <h4>Leafy screening</h4>
              <p>The current screening episode is attached to the same patient record.</p>
            </div>
            <span className="patient-record-section-note">
              {screening.reportDate ? `Reported ${formatDateTime(screening.reportDate)}` : "Current screening"}
            </span>
          </div>

          <div className="patient-record-chip-row">
            {Array.isArray(screening.biradsValues) && screening.biradsValues.length > 0 ? (
              <span className="patient-record-chip success">BI-RADS: {screening.biradsValues.join(", ")}</span>
            ) : null}
            <span className="patient-record-chip neutral">
              {screening.numberImages || 0} screening view{Number(screening.numberImages || 0) === 1 ? "" : "s"}
            </span>
            {Array.isArray(screening.keywords) && screening.keywords.length > 0 ? (
              <span className="patient-record-chip keyword">{screening.keywords.slice(0, 4).join(" · ")}</span>
            ) : null}
          </div>

          {Array.isArray(screening.series) && screening.series.length > 0 ? (
            <div className="patient-record-screening-strip">
              {screening.series.map((image) => (
                <article key={image.id} className="patient-record-screening-card compact">
                  <div className="patient-record-screening-thumb compact">
                    {image.thumbnailSrc ? (
                      <img src={image.thumbnailSrc} alt={`${image.laterality || ""} ${image.viewPosition || ""}`.trim() || "Screening image"} />
                    ) : (
                      <div className="patient-record-screening-placeholder">Image</div>
                    )}
                  </div>
                  <div className="patient-record-screening-card-body compact">
                    <strong>{[image.laterality, image.viewPosition].filter(Boolean).join(" · ") || "Mammography view"}</strong>
                    <span>{image.acquiredAt ? formatDateShort(image.acquiredAt) : "Date unavailable"}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : null}

          {screeningNarratives.length > 0 ? (
            <div className="patient-record-screening-notes">
              {screeningNarratives.map((entry) => (
                <article key={entry.title} className="patient-record-screening-note">
                  <h5>{entry.title}</h5>
                  <p>{compactText(entry.body, 280)}</p>
                </article>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
