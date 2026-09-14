"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Dashboard, DailyReport } from "@/lib/types";

const JOBS = [
  { id: "collect-github", label: "Собрать GitHub" },
  { id: "process-pipeline", label: "Прогнать pipeline" },
  { id: "snapshot-repositories", label: "Снять снапшоты" },
  { id: "daily-radar", label: "Построить отчёт" },
];

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [report, setReport] = useState<DailyReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  async function load() {
    try {
      setData(await api.dashboard());
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
    try {
      setReport(await api.latestReport());
    } catch {
      setReport(null);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function runJob(job: string) {
    try {
      await api.trigger(job, job === "daily-radar");
      setToast(`Задача «${job}» поставлена в очередь`);
      setTimeout(() => setToast(null), 3000);
      setTimeout(load, 4000);
    } catch (err) {
      setToast(`Ошибка: ${(err as Error).message}`);
      setTimeout(() => setToast(null), 5000);
    }
  }

  if (error) {
    return (
      <>
        <div className="page-head">
          <h1>Сводка</h1>
        </div>
        <div className="error-box">
          Бэкенд недоступен: {error}
          <div style={{ marginTop: 8, color: "var(--tx-3)" }}>
            Проверь, что API поднят и переменная API_URL указывает на него.
          </div>
        </div>
      </>
    );
  }

  if (!data) {
    return (
      <>
        <div className="page-head">
          <h1>Сводка</h1>
        </div>
        <div className="skeleton" />
        <div className="skeleton" />
      </>
    );
  }

  const cost = data.cost;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Сводка</h1>
          <div className="subtitle">
            {data.findings_24h} находок за сутки · {data.pending_raw_items} в очереди на разбор
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {JOBS.map((job) => (
            <button key={job.id} className="btn-ghost btn-sm" onClick={() => runJob(job.id)}>
              {job.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-4">
        <div className="stat">
          <div className="stat-label">Critical</div>
          <div className="stat-value" style={{ color: "var(--critical)" }}>
            {data.by_status.CRITICAL ?? 0}
          </div>
          <div className="stat-note">требуют внимания сейчас</div>
        </div>
        <div className="stat">
          <div className="stat-label">Recommended</div>
          <div className="stat-value" style={{ color: "var(--recommended)" }}>
            {data.by_status.RECOMMENDED ?? 0}
          </div>
          <div className="stat-note">заметное улучшение</div>
        </div>
        <div className="stat">
          <div className="stat-label">Review Later</div>
          <div className="stat-value" style={{ color: "var(--review)" }}>
            {data.by_status.REVIEW_LATER ?? 0}
          </div>
          <div className="stat-note">{data.review_queue_due} срок подошёл</div>
        </div>
        <div className="stat">
          <div className="stat-label">Всего находок</div>
          <div className="stat-value">{data.findings_total}</div>
          <div className="stat-note">
            отклонено {data.by_status.REJECTED ?? 0} · архив {data.by_status.ARCHIVED ?? 0}
          </div>
        </div>
      </div>

      <div className="grid grid-2" style={{ marginTop: 24 }}>
        <div className="card">
          <h2>Последний отчёт</h2>
          {report ? (
            <>
              <div className="subtitle" style={{ marginBottom: 12 }}>
                {report.report_date} · Telegram {report.telegram_messages_processed} · GitHub{" "}
                {report.github_candidates} · после AI {report.after_ai_filter}
              </div>
              {report.is_empty ? (
                <div className="reason" style={{ fontStyle: "normal" }}>
                  Сегодня значимых находок нет. Это нормальный исход — фильтр отработал
                  и не стал заполнять отчёт шумом.
                </div>
              ) : (
                <div className="chips">
                  <span className="chip">CRITICAL {report.critical_count}</span>
                  <span className="chip">RECOMMENDED {report.recommended_count}</span>
                  <span className="chip">REVIEW LATER {report.review_later_count}</span>
                  <span className="chip">отклонено {report.rejected_count}</span>
                  <span className="chip">дубликатов {report.duplicates_count}</span>
                </div>
              )}
              <div style={{ marginTop: 14 }}>
                <Link className="btn-ghost btn-sm" href="/reports">
                  Все отчёты
                </Link>
              </div>
            </>
          ) : (
            <div className="subtitle">Отчётов пока нет — первый появится после 21:00.</div>
          )}
        </div>

        <div className="card">
          <h2>Стоимость</h2>
          <div className="grid grid-4" style={{ marginTop: 12, gap: 10 }}>
            <div>
              <div className="stat-label">Сегодня</div>
              <div className="stat-value" style={{ fontSize: 20 }}>
                ${cost.today.total_cost_usd.toFixed(4)}
              </div>
            </div>
            <div>
              <div className="stat-label">Месяц</div>
              <div className="stat-value" style={{ fontSize: 20 }}>
                ${cost.month.total_cost_usd.toFixed(3)}
              </div>
            </div>
            <div>
              <div className="stat-label">Прогноз</div>
              <div className="stat-value" style={{ fontSize: 20 }}>
                ${cost.projected_month_usd.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="stat-label">Deep сегодня</div>
              <div className="stat-value" style={{ fontSize: 20 }}>
                {cost.deep_analyses_today}/{cost.deep_analyses_limit}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 24 }}>
        <h2>По проектам</h2>
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Проект</th>
              <th className="num">Находок</th>
              <th className="num">Средний radar</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.by_project.map((p) => (
              <tr key={p.slug}>
                <td style={{ color: "var(--tx)" }}>{p.name}</td>
                <td className="num">{p.total}</td>
                <td className="num">{p.avg_score.toFixed(2)}</td>
                <td className="num">
                  <Link className="btn-ghost btn-sm" href={`/radar?project=${p.slug}`}>
                    Открыть
                  </Link>
                </td>
              </tr>
            ))}
            {data.by_project.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ color: "var(--tx-3)" }}>
                  Проектов пока нет. Заведи первый в разделе Projects — без профиля
                  оценивать находки не относительно чего.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h2>Коллекторы</h2>
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Коллектор</th>
              <th>Статус</th>
              <th>Запуск</th>
              <th className="num">Получено</th>
              <th className="num">Новых</th>
              <th className="num">API</th>
              <th className="num">Лимит</th>
            </tr>
          </thead>
          <tbody>
            {data.collectors.map((run, i) => (
              <tr key={i}>
                <td className="mono">{run.collector}</td>
                <td style={{ color: run.status === "ok" ? "var(--recommended)" : "var(--review)" }}>
                  {run.status}
                </td>
                <td>{new Date(run.started_at).toLocaleString("ru-RU")}</td>
                <td className="num">{run.items_fetched}</td>
                <td className="num">{run.items_new}</td>
                <td className="num">{run.api_requests}</td>
                <td className="num">{run.rate_limit_remaining ?? "—"}</td>
              </tr>
            ))}
            {data.collectors.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ color: "var(--tx-3)" }}>
                  Сбор ещё не запускался.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {toast ? <div className="toast">{toast}</div> : null}
    </>
  );
}
