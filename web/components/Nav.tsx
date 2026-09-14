"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { STATUS_LABELS, STATUS_ORDER } from "@/lib/labels";

const MAIN = [
  { href: "/", label: "Сводка" },
  { href: "/projects", label: "Проекты" },
];

const RADAR = STATUS_ORDER.map((key) => ({
  href: `/radar?status=${key}`,
  label: STATUS_LABELS[key],
  key,
}));

const OPS = [
  { href: "/sources", label: "Источники" },
  { href: "/reports", label: "Отчёты" },
  { href: "/settings", label: "Настройки" },
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
        <div className="nav-label">Радар</div>
        {RADAR.map((item) => (
          <Link key={item.key} href={item.href} className="nav-link">
            <span>{item.label}</span>
            {counts[item.key] ? <span className="nav-count">{counts[item.key]}</span> : null}
          </Link>
        ))}
      </div>

      <div className="nav-group">
        <div className="nav-label">Обслуживание</div>
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
