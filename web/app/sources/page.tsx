"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      setSources(await api.sources());
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function toggle(source: Source) {
    try {
      await api.toggleSource(source.id, !source.is_active);
      load();
    } catch (err) {
      setToast((err as Error).message);
      setTimeout(() => setToast(null), 4000);
    }
  }

  async function reset(source: Source) {
    if (!confirm(`Сбросить курсор для «${source.title || source.external_id}»? Источник будет перечитан заново.`))
      return;
    try {
      await api.resetCursor(source.id);
      setToast("Курсор сброшен");
      setTimeout(() => setToast(null), 3000);
      load();
    } catch (err) {
      setToast((err as Error).message);
      setTimeout(() => setToast(null), 4000);
    }
  }

  const telegram = sources.filter((s) => s.kind === "telegram");
  const github = sources.filter((s) => s.kind.startsWith("github"));
  const other = sources.filter((s) => s.kind !== "telegram" && !s.kind.startsWith("github"));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Sources</h1>
          <div className="subtitle">
            {sources.length} источников · архитектура расширяемая: Habr, Reddit, HN и RSS
            подключаются реализацией одного интерфейса
          </div>
        </div>
      </div>

      {error ? <div className="error-box">{error}</div> : null}
      {loading ? <div className="skeleton" /> : null}

      <Section title="Telegram" hint="User-session через Telethon, отдельный процесс, курсор по min_id" items={telegram} onToggle={toggle} onReset={reset} />
      <Section title="GitHub" hint="Собственный радар: search 30 запросов/мин, максимум 1000 результатов на запрос, ETag экономит лимит" items={github} onToggle={toggle} onReset={reset} />
      {other.length > 0 ? (
        <Section title="Прочее" hint="" items={other} onToggle={toggle} onReset={reset} />
      ) : null}

      {!loading && sources.length === 0 ? (
        <div className="empty">
          <div className="empty-title">Источников нет</div>
          Telegram-источники заводятся из TELEGRAM_SOURCE_IDS при первом запуске
          коллектора. GitHub-источники создаются автоматически при первом прогоне радара.
        </div>
      ) : null}

      {toast ? <div className="toast">{toast}</div> : null}
    </>
  );
}

function Section({
  title,
  hint,
  items,
  onToggle,
  onReset,
}: {
  title: string;
  hint: string;
  items: Source[];
  onToggle: (s: Source) => void;
  onReset: (s: Source) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>{title}</h2>
      {hint ? (
        <div className="subtitle" style={{ marginBottom: 12 }}>
          {hint}
        </div>
      ) : null}
      <table>
        <thead>
          <tr>
            <th>Источник</th>
            <th>Статус</th>
            <th>Последний сбор</th>
            <th className="num">Собрано</th>
            <th className="num">Курсор</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id}>
              <td>
                <div style={{ color: "var(--tx)" }}>{s.title || s.external_id}</div>
                <div className="mono" style={{ color: "var(--tx-3)" }}>
                  {s.external_id}
                </div>
                {s.last_error ? (
                  <div style={{ color: "var(--review)", fontSize: 12, marginTop: 4 }}>
                    {s.last_error}
                  </div>
                ) : null}
              </td>
              <td>
                {s.paused_until ? (
                  <span className="chip" style={{ color: "var(--review)" }}>
                    пауза до {new Date(s.paused_until).toLocaleTimeString("ru-RU")}
                  </span>
                ) : s.is_active ? (
                  <span style={{ color: "var(--recommended)" }}>активен</span>
                ) : (
                  <span style={{ color: "var(--tx-3)" }}>выключен</span>
                )}
              </td>
              <td>{s.last_run_at ? new Date(s.last_run_at).toLocaleString("ru-RU") : "—"}</td>
              <td className="num">{s.items_collected}</td>
              <td className="num mono">{s.last_item_id || "—"}</td>
              <td className="num">
                <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button className="btn-ghost btn-sm" onClick={() => onToggle(s)}>
                    {s.is_active ? "Выключить" : "Включить"}
                  </button>
                  <button className="btn-ghost btn-sm" onClick={() => onReset(s)}>
                    Сброс
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
