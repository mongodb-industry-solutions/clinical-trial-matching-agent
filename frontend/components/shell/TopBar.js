"use client";

import { usePathname } from "next/navigation";

const PAGE_META = {
  "/discovery": {
    title: "Trial Discovery",
    badge: "Canonical Catalog · CT.gov Intake",
  },
  "/patients": {
    title: "Patient Evidence",
    badge: "Leafy + Synthea · Mongo Graph",
  },
  "/match": { title: "AI Match Copilot", badge: "Patient ↔ Trial Screening" },
  "/literature": {
    title: "Literature Intelligence",
    badge: "Breast Oncology · PubMed",
  },
  "/governance": {
    title: "Governance & Proof",
    badge: "Benchmarks · Audit Trail",
  },
};

export default function TopBar() {
  const pathname = usePathname();
  const currentPath = String(pathname || "/");

  const segment = "/" + (currentPath.split("/")[1] || "discovery");
  const meta = PAGE_META[segment] || {
    title: "Clinical Trial Intelligence",
    badge: null,
  };

  return (
    <header className="mdb-topbar">
      <div className="mdb-topbar-title">{meta.title}</div>
      {meta.badge && <span className="mdb-topbar-badge">{meta.badge}</span>}
    </header>
  );
}
