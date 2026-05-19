"use client";

import { usePathname } from "next/navigation";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";

function isCompatWorkbenchPath(pathname) {
  return String(pathname || "").startsWith("/pharmaNavigator");
}

export default function AppChrome({ children }) {
  const pathname = usePathname();

  if (isCompatWorkbenchPath(pathname)) {
    return children;
  }

  return (
    <div className="mdb-shell">
      <Sidebar />
      <main className="mdb-main">
        <TopBar />
        <div className="mdb-content">
          {children}
        </div>
      </main>
    </div>
  );
}
