"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import Image from "next/image";

const NAV_ITEMS = [
  {
    href: "/pharmaNavigator",
    label: "Clinical Trials Navigator",
    description: "Ingestion, patient graph, and bidirectional matching",
    icon: (
      <svg
        className="mdb-nav-icon"
        viewBox="0 0 16 16"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M3 3h10v10H3z" stroke="currentColor" strokeWidth="1.3" />
        <path
          d="M6 1.75v12.5M10 1.75v12.5M1.75 6h12.5M1.75 10h12.5"
          stroke="currentColor"
          strokeWidth="1.1"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
];

export default function Sidebar() {
  const pathname = String(usePathname() || "");

  return (
    <aside className="mdb-sidebar">
      <Link href="/pharmaNavigator" className="mdb-sidebar-brand">
        <Image
          src="/mongodb-logomark-white.svg"
          alt="MongoDB"
          width={20}
          height={20}
          style={{ height: 20, width: "auto" }}
          priority
        />
        <span className="mdb-sidebar-brand-label">
          Clinical Trials
          <br />
          Navigator
        </span>
      </Link>

      <div className="mdb-sidebar-section">
        <div className="mdb-sidebar-section-label">WORKFLOW</div>
        {NAV_ITEMS.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`mdb-nav-item ${active ? "active" : ""}`}
            >
              {item.icon}
              <span style={{ flex: 1 }}>
                <div>{item.label}</div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 500,
                    color: active ? "rgba(255,255,255,0.72)" : "#94a3b8",
                    marginTop: 2,
                  }}
                >
                  {item.description}
                </div>
              </span>
            </Link>
          );
        })}
      </div>

      <div className="mdb-sidebar-footer">
        <div className="mdb-sidebar-version">
          Unified Navigator · v1.0
        </div>
      </div>
    </aside>
  );
}
