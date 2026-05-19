"use client";

import Image from "next/image";

const PRIMARY_ITEMS = [
  "Project Overview",
  "Screening List",
  "Advanced Search",
  "Reports",
  "More",
];

export default function LeafyHospitalHeader() {
  return (
    <header className="leafy-header">
      <div className="leafy-header__inner">
        <a href="/pharmaNavigator" className="leafy-header__brand">
          <Image
            src="/leafy_logo.png"
            alt="Leafy Hospital"
            width={182}
            height={48}
            priority
          />
        </a>

        <nav className="leafy-header__nav" aria-label="Leafy Hospital">
          {PRIMARY_ITEMS.map((item) => (
            <span key={item} className="leafy-header__nav-item">
              {item}
            </span>
          ))}
        </nav>

        <div className="leafy-header__actions">
          <button
            type="button"
            className="leafy-header__icon"
            aria-label="Preferences"
          >
            <Image
              src="/leafy_preferences.png"
              alt=""
              width={24}
              height={24}
            />
          </button>
          <div className="leafy-header__user-card">
            <Image
              src="/leafy_admin.png"
              alt="Eddie Grant"
              width={38}
              height={38}
            />
            <div className="leafy-header__user-copy">
              <strong>Eddie Grant</strong>
              <span>Supervisor</span>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
