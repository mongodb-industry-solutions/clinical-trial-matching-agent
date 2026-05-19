import React, { useContext, useEffect, useMemo, useState } from "react";
import { AppContext } from "../../context/AppContext";
import { APILoadInitialAppointments } from "../../services/APIService";

const DEFAULT_FILTERS = Object.freeze({
  searchQuery: "",
  facets: [{ category: "resource.status", value: ["fulfilled"] }],
});

const capitalizeWords = (name) => {
  const normalizedName = typeof name === "string" ? name.trim() : "";
  if (!normalizedName) {
    return "";
  }

  return normalizedName
    .split(" ")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
};

const formatAppointmentDate = (startValue) => {
  const parsed = startValue ? new Date(startValue) : null;
  if (!parsed || Number.isNaN(parsed.getTime())) {
    return "Unknown appointment date";
  }
  return parsed.toLocaleString();
};

const extractPatientCandidate = (appointment) => {
  const metadata = appointment?.metadata?.extraSearchParams || {};
  const subjectMeta = metadata?.subject || {};
  const resource = appointment?.resource || {};
  const subjectDisplay = Array.isArray(subjectMeta?.display) ? subjectMeta.display[0] : {};

  const fallbackPatientName = [subjectDisplay?.given?.join?.(" "), subjectDisplay?.family]
    .filter(Boolean)
    .join(" ")
    .trim();

  const patientName = capitalizeWords(
    subjectMeta?.patientName || fallbackPatientName || resource?.subject?.display || ""
  ) || "Unknown patient";

  return {
    id: String(appointment?._id || ""),
    appointmentId: String(appointment?._id || ""),
    patientId: String(resource?.subject?.reference || ""),
    patientName,
    center: String(resource?.location?.display || ""),
    practitioner: capitalizeWords(
      metadata?.actor?.actorName || resource?.participant?.[0]?.actor?.display || ""
    ),
    reportId: "",
    status: String(resource?.status || "unknown"),
    start: resource?.start || "",
    startLabel: formatAppointmentDate(resource?.start),
    sourceAppointment: appointment,
  };
};

function PatientContextPicker({
  centerHint = "",
  title = "Recent patients",
  subtitle = "Select a recent attended patient, filter the shortlist, or load one at random.",
  emptyMessage = "No recent attended patients are available for this workspace yet.",
  onSelectPatient = () => {},
  maxItems = 10,
}) {
  const { preferences, currentAppUser, currentCenter } = useContext(AppContext);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [recentPatients, setRecentPatients] = useState([]);

  const resolvedCenter = centerHint || currentCenter || "Mayo Clinic Hospital - Phoenix";

  useEffect(() => {
    let isActive = true;

    const loadPatients = async () => {
      setLoading(true);
      setError("");

      try {
        const { initialAppointments = [] } = await APILoadInitialAppointments(
          DEFAULT_FILTERS,
          "start",
          "desc",
          preferences,
          currentAppUser,
          resolvedCenter
        );

        if (!isActive) {
          return;
        }

        const uniqueCandidates = [];
        const seen = new Set();

        (initialAppointments || []).forEach((appointment) => {
          const candidate = extractPatientCandidate(appointment);
          const dedupeKey = candidate.patientId || candidate.appointmentId;
          if (!dedupeKey || seen.has(dedupeKey)) {
            return;
          }
          seen.add(dedupeKey);
          uniqueCandidates.push(candidate);
        });

        setRecentPatients(uniqueCandidates.slice(0, Math.max(1, maxItems)));
      } catch (loadError) {
        if (!isActive) {
          return;
        }
        console.error("Failed to load patient context candidates:", loadError);
        setError(loadError?.response?.data?.error || loadError?.message || "Unable to load recent patients.");
        setRecentPatients([]);
      } finally {
        if (isActive) {
          setLoading(false);
        }
      }
    };

    loadPatients();

    return () => {
      isActive = false;
    };
  }, [currentAppUser, maxItems, preferences, resolvedCenter]);

  const filteredPatients = useMemo(() => {
    const normalizedQuery = String(query || "").trim().toLowerCase();
    if (!normalizedQuery) {
      return recentPatients;
    }

    return recentPatients.filter((patient) =>
      [
        patient.patientName,
        patient.patientId,
        patient.appointmentId,
        patient.center,
        patient.practitioner,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalizedQuery))
    );
  }, [query, recentPatients]);

  const handlePickRandom = () => {
    if (!filteredPatients.length) {
      return;
    }
    const randomCandidate = filteredPatients[Math.floor(Math.random() * filteredPatients.length)];
    onSelectPatient(randomCandidate);
  };

  return (
    <div className="patient-context-picker">
      <div className="patient-context-picker__header">
        <div>
          <strong>{title}</strong>
          <p>{subtitle}</p>
        </div>
        <div className="patient-context-picker__actions">
          <button type="button" onClick={handlePickRandom} disabled={loading || filteredPatients.length === 0}>
            Random patient
          </button>
        </div>
      </div>

      <div className="patient-context-picker__toolbar">
        <input
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter by patient, appointment, center, or practitioner"
          className="patient-context-picker__search"
          disabled={loading}
        />
        <span className="patient-context-picker__count">
          {filteredPatients.length} of {recentPatients.length} visible
        </span>
      </div>

      {error ? <div className="patient-context-picker__feedback error">{error}</div> : null}
      {loading ? <div className="patient-context-picker__feedback">Loading recent patients...</div> : null}
      {!loading && !error && filteredPatients.length === 0 ? (
        <div className="patient-context-picker__feedback">{emptyMessage}</div>
      ) : null}

      {!loading && filteredPatients.length > 0 ? (
        <div className="patient-context-picker__list">
          {filteredPatients.map((patient) => (
            <button
              type="button"
              key={`${patient.patientId || patient.appointmentId}`}
              className="patient-context-picker__item"
              onClick={() => onSelectPatient(patient)}
            >
              <div className="patient-context-picker__avatar" aria-hidden="true">
                {String(patient.patientName || "P").trim().slice(0, 1).toUpperCase()}
              </div>
              <div className="patient-context-picker__body">
                <div className="patient-context-picker__topline">
                  <strong>{patient.patientName}</strong>
                  <span>{patient.startLabel}</span>
                </div>
                <div className="patient-context-picker__meta">
                  <span>{patient.center || resolvedCenter}</span>
                  {patient.practitioner ? <span>{patient.practitioner}</span> : null}
                </div>
                <div className="patient-context-picker__ids">
                  <code>Appointment {patient.appointmentId || "-"}</code>
                  <code>Patient {patient.patientId || "-"}</code>
                </div>
              </div>
              <span className="patient-context-picker__cta">Use context</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default PatientContextPicker;
