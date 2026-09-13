"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const MAIN = [
  { href: "/", label: "Dashboard" },
  { href: "/projects", label: "Projects" },
];

const RADAR = [
  { href: "/radar?status=CRITICAL", label: "Critical", key: "CRITICAL" },
  { href: "/radar?status=RECOMMENDED", label: "Recommended", key: "RECOMMENDED" },
  { href: "/radar?status=REVIEW_LATER", label: "Review Later", key: "REVIEW_LATER" },
  { href: "/radar?status=ARCHIVED", label: "Archive", key: "ARCHIVED" },
  { href: "/radar?status=REJECTED", label: "Rejected", key: "REJECTED" },
];

const OPS = [
  { href: "/sources", label: "Sources" },
  { href: "/reports", label: "Reports" },
  { href: "/settings", label: "Settings" },
];

export default function Nav() {
  const pathname = usePathname();
  const [counts, setCounts] = useState<Record<string, number>>({});

  useEffect(() => {
    api
      .dashboard()
      .then((d) => setCounts(d.by_status || {}))
      .catch(() => setCounts({}));
  }, [pathname]);

  return (
    <aside className="sidebar">
      <Link href="/" className="brand">
        <div className="brand-mark">Зодчий</div>
        <div className="brand-name">Project Radar</div>
      </Link>

      <div className="nav-group">
        {MAIN.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`nav-link ${pathname === item.href ? "active" : ""}`}
          >
            {item.label}
          </Link>
        ))}
      </div>

      <div className="nav-group">
        <div className="nav-label">Radar</div>
        {RADAR.map((item) => (
          <Link key={item.key} href={item.href} className="nav-link">
            <span>{item.label}</span>
            {counts[item.key] ? <span className="nav-count">{counts[item.key]}</span> : null}
          </Link>
        ))}
      </div>

      <div className="nav-group">
        <div className="nav-label">Ops</div>
        {OPS.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`nav-link ${pathname === item.href ? "active" : ""}`}
          >
            {item.label}
          </Link>
        ))}
      </div>
    </aside>
  );
}
