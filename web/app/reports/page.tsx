"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DailyReport } from "@/lib/types";

export default function ReportsPage() {
  const [reports, setReports] = useState<DailyReport[]>([]);
  const [selected, setSelected] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    api
      .reports()
      .then((list) => {
        setReports(list);
        setSelected(list[0] ?? null);
        setError(null);
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, []);

  async function copyReport() {
    if (!selected) return;
    try {
      await navigator.clipboard.writeText(selected.body_markdown);
      setToast("Отчёт скопирован");
    } catch {
      setToast("Не удалось скопировать");
    }
    setTimeout(() => setToast(null), 2500);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Отчёты</h1>
          <div className="subtitle">
            Daily Radar · окно — предыдущие 24 часа · Asia/Aqtau
          </div>
        </div>
        {selected ? (
          <button className="btn-ghost btn-sm" onClick={copyReport}>
            Скопировать отчёт
          </button>
        ) : null}
      </div>

      {error ? <div className="error-box">{error}</div> : null}
      {loading ? <div className="skeleton" /> : null}

      {!loading && reports.length === 0 ? (
        <div className="empty">
          <div className="empty-title">Отчётов пока нет</div>
          Первый появится после запуска задачи build_daily_radar — по расписанию
          в 21:00 или вручную с дашборда.
        </div>
      ) : null}

      {reports.length > 0 ? (
        <div style={{ display: "grid", gridTemplateColumns: "230px 1fr", gap: 16 }}>
          <div>
            {reports.map((r) => (
              <button
                key={r.id}
                onClick={() => setSelected(r)}
                className={selected?.id === r.id ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
                style={{ width: "100%", justifyContent: "space-between", marginBottom: 6 }}
              >
                <span>{r.report_date}</span>
                <span>{r.is_empty ? "—" : r.critical_count + r.recommended_count}</span>
              </button>
            ))}
          </div>

          <div className="card">
            {selected ? (
              <>
                <div className="grid grid-4" style={{ gap: 10, marginBottom: 18 }}>
                  <Stat label="Telegram" value={selected.telegram_messages_processed} />
                  <Stat label="GitHub" value={selected.github_candidates} />
                  <Stat label="После фильтра" value={selected.after_cheap_filter} />
                  <Stat label="После AI" value={selected.after_ai_filter} />
                  <Stat label="Критично" value={selected.critical_count} color="var(--critical)" />
                  <Stat label="Рекомендовано" value={selected.recommended_count} color="var(--recommended)" />
                  <Stat label="Отложено" value={selected.review_later_count} color="var(--review)" />
                  <Stat label="Отклонено" value={selected.rejected_count} />
                </div>
                <div className="markdown-body">{selected.body_markdown}</div>
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      {toast ? <div className="toast">{toast}</div> : null}
    </>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ fontSize: 20, color }}>
        {value}
      </div>
    </div>
  );
}
