"use client";

import { useEffect } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import L from "leaflet";

// Fix Leaflet's broken default icon path in bundlers
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

const MDB_GREEN_ICON = new L.Icon({
  iconUrl:
    "data:image/svg+xml;base64," +
    btoa(`<svg xmlns="http://www.w3.org/2000/svg" width="24" height="36" viewBox="0 0 24 36">
  <path d="M12 0C5.4 0 0 5.4 0 12c0 8.4 12 24 12 24s12-15.6 12-24C24 5.4 18.6 0 12 0z" fill="#00684A"/>
  <circle cx="12" cy="12" r="5" fill="#00ED64"/>
</svg>`),
  iconSize: [24, 36],
  iconAnchor: [12, 36],
  popupAnchor: [0, -36],
});

function FitBounds({ locations }) {
  const map = useMap();
  useEffect(() => {
    if (locations.length === 1) {
      map.setView([locations[0].lat, locations[0].lng], 10);
    } else if (locations.length > 1) {
      const bounds = L.latLngBounds(locations.map((l) => [l.lat, l.lng]));
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 8 });
    }
  }, [locations, map]);
  return null;
}

export default function TrialMapInner({ locations, height = 280 }) {
  if (!locations || locations.length === 0) {
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
          flexDirection: "column",
          gap: 8,
          color: "var(--mdb-slate-30)",
        }}
      >
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
          <circle cx="12" cy="10" r="3"/>
        </svg>
        <span style={{ fontSize: 12 }}>No location data available</span>
      </div>
    );
  }

  const center =
    locations.length === 1
      ? [locations[0].lat, locations[0].lng]
      : [
          locations.reduce((s, l) => s + l.lat, 0) / locations.length,
          locations.reduce((s, l) => s + l.lng, 0) / locations.length,
        ];

  return (
    <MapContainer
      center={center}
      zoom={5}
      style={{ height, width: "100%", borderRadius: "var(--mdb-radius-lg)", zIndex: 1 }}
      scrollWheelZoom={false}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds locations={locations} />
      {locations.map((loc, i) => (
        <Marker key={i} position={[loc.lat, loc.lng]} icon={MDB_GREEN_ICON}>
          <Popup>
            <div style={{ fontSize: 12, lineHeight: 1.5 }}>
              {loc.facility && <strong style={{ display: "block", marginBottom: 2 }}>{loc.facility}</strong>}
              {[loc.city, loc.state, loc.country].filter(Boolean).join(", ")}
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
