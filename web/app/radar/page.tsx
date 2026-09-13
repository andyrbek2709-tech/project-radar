"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Finding, Project } from "@/lib/types";
import FindingCard from "@/components/FindingCard";

const STATUSES = [
  { value: "", label: "Все статусы" },
  { value: "CRITICAL", label: "Critical" },
  { value: "RECOMMENDED", label: "Recommended" },
  { value: "REVIEW_LATER", label: "Review Later" },
  { value: "ARCHIVED", label: "Archive" },
  { value: "REJECTED", label: "Rejected" },
];

const TITLES: Record<string, string> = {
  CRITICAL: "Critical",
  RECOMMENDED: "Recommended",
  REVIEW_LATER: "Review Later",
  ARCHIVED: "Archive",
  REJECTED: "Rejected",
};

const EMPTY_HINTS: Record<string, string> = {
  CRITICAL: "Ничего критичного. Это хороший знак, а не поломка.",
  RECOMMENDED: "Рекомендаций пока нет.",
  REVIEW_LATER: "Отложенного нет.",
  ARCHIVED: "Архив пуст.",
  REJECTED: "Отклонённого нет — либо сбор ещё не запускался.",
};

function RadarContent() {
  const router = useRouter();
  const params = useSearchParams();

  const status = params.get("status") || "";
  const project = params.get("project") || "";
  const search = params.get("search") || "";

  const [items, setItems] = useState<Finding[]>([]);
  const [total, setTotal] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState(search);

  useEffect(() => {
    api.projects().then(setProjects).catch(() => setProjects([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await api.findings({ status, project, search, limit: 50 });
      setItems(page.items);
      setTotal(page.total);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [status, project, search]);

  useEffect(() => {
    load();
  }, [load]);

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.push(`/radar?${next.toString()}`);
  }

  const title = status ? TITLES[status] || "Radar" : "Radar";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <div className="subtitle">
            {loading ? "загрузка…" : `${total} ${plural(total)}`}
            {project ? ` · проект ${project}` : ""}
          </div>
        </div>
      </div>

      <div className="filters">
        <select value={status} onChange={(e) => setParam("status", e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>

        <select value={project} onChange={(e) => setParam("project", e.target.value)}>
          <option value="">Все проекты</option>
          {projects.map((p) => (
            <option key={p.slug} value={p.slug}>
              {p.name}
            </option>
          ))}
        </select>

        <input
          type="search"
          placeholder="Поиск по названию…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") setParam("search", query);
          }}
        />

        <button className="btn-ghost btn-sm" onClick={() => setParam("search", query)}>
          Найти
        </button>
        <button className="btn-ghost btn-sm" onClick={() => router.push("/radar")}>
          Сбросить
        </button>
      </div>

      {error ? <div className="error-box">{error}</div> : null}

      {loading ? (
        <>
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </>
      ) : items.length === 0 ? (
        <div className="empty">
          <div className="empty-title">Пусто</div>
          {EMPTY_HINTS[status] || "Находок по этим фильтрам нет."}
        </div>
      ) : (
        items.map((finding) => (
          <FindingCard key={`${finding.id}-${finding.match?.project_id ?? ""}`} finding={finding} />
        ))
      )}
    </>
  );
}

function plural(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "находка";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "находки";
  return "находок";
}

export default function RadarPage() {
  return (
    <Suspense fallback={<div className="skeleton" />}>
      <RadarContent />
    </Suspense>
  );
}
