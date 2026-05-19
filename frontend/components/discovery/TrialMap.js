"use client";

import dynamic from "next/dynamic";
import { useState, useEffect } from "react";
import "leaflet/dist/leaflet.css";

const TrialMapInner = dynamic(() => import("./TrialMapInner"), {
  ssr: false,
  loading: () => (
    <div
      style={{
        height: 280,
        background: "var(--mdb-bg-subtle)",
        border: "1px solid var(--mdb-border)",
        borderRadius: "var(--mdb-radius-lg)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--mdb-slate-30)",
        fontSize: 13,
        gap: 8,
      }}
    >
      <span className="mdb-spinner" />
      Loading map…
    </div>
  ),
});

export default function TrialMap({ nctId, height = 280 }) {
  const [locations, setLocations] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!nctId) { setLocations([]); setLoading(false); return; }
    setLoading(true);
    setLocations(null);
    fetch(`/api/trial-geo/${nctId}`)
      .then((r) => r.json())
      .then((data) => setLocations(data?.locations || []))
      .catch(() => setLocations([]))
      .finally(() => setLoading(false));
  }, [nctId]);

  if (loading) {
    return (
      <div
        style={{
          height,
          background: "var(--mdb-bg-subtle)",
          border: "1px solid var(--mdb-border)",
          borderRadius: "var(--mdb-radius-lg)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--mdb-slate-30)",
          fontSize: 13,
          gap: 8,
        }}
      >
        <span className="mdb-spinner" />
        Fetching trial locations…
      </div>
    );
  }

  return <TrialMapInner locations={locations || []} height={height} />;
}
