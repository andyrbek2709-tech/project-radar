"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, any> | null>(null);
  const [cost, setCost] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .runtimeSettings()
      .then(setSettings)
      .catch((err) => setError((err as Error).message));
    api.cost().then(setCost).catch(() => setCost(null));
  }, []);

  if (error) {
    return (
      <>
        <div className="page-head">
          <h1>Настройки</h1>
        </div>
        <div className="error-box">{error}</div>
      </>
    );
  }

  if (!settings) {
    return (
      <>
        <div className="page-head">
          <h1>Настройки</h1>
        </div>
        <div className="skeleton" />
      </>
    );
  }

  const t = settings.thresholds || {};

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Настройки</h1>
          <div className="subtitle">
            Действующая конфигурация. Секретов здесь нет — только флаги «настроено / не настроено»
          </div>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <h2>Интеграции</h2>
          <table style={{ marginTop: 12 }}>
            <tbody>
              <Flag label="GitHub token" ok={settings.github_configured} />
              <Flag label="GitHub search" ok={settings.github_search_enabled} />
              <Flag label="Telegram session" ok={settings.telegram_configured} />
              <Flag label="Telegram сбор" ok={settings.telegram_enabled} />
              <Flag label="Groq API key" ok={settings.groq_configured} />
              <Flag label="Groq включён" ok={settings.groq_enabled} />
              <Flag label="OpenAI API key" ok={settings.openai_configured} />
              <Flag
                label="Deep analysis AUTO"
                ok={settings.openai_deep_analysis_enabled}
                offNote="ручной режим через СКОПИРОВАТЬ КОНТЕКСТ"
              />
            </tbody>
          </table>
        </div>

        <div className="card">
          <h2>Расписание и модели</h2>
          <table style={{ marginTop: 12 }}>
            <tbody>
              <Row label="Окружение" value={settings.app_env} />
              <Row label="Таймзона" value={settings.timezone} />
              <Row label="Daily Radar" value={`${settings.radar_daily_time} ${settings.radar_enabled ? "" : "(выключен)"}`} />
              <Row label="Telegram, интервал" value={`${settings.telegram_scan_interval_minutes} мин`} />
              <Row label="GitHub, интервал" value={`${settings.github_scan_interval_minutes} мин`} />
              <Row label="Groq модель" value={settings.groq_model} />
              <Row label="Эмбеддинги" value={`${settings.embedding_provider} · ${settings.embedding_model} · ${settings.embedding_dim}d`} />
              <Row label="Deep analyses / день" value={settings.max_deep_analyses_per_day} />
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Пороги</h2>
        <div className="subtitle" style={{ marginBottom: 12 }}>
          Меняются в таблице settings без редеплоя. Строгие пороги = короткие отчёты;
          это признак того, что фильтр работает, а не поломки.
        </div>
        <table>
          <tbody>
            <Row label="MIN_RELEVANCE_SCORE" value={t.min_relevance} mono />
            <Row label="MIN_DEEP_ANALYSIS_SCORE" value={t.min_deep_analysis} mono />
            <Row label="CRITICAL_SCORE" value={t.critical} mono />
            <Row label="RECOMMENDED_SCORE" value={t.recommended} mono />
            <Row label="REVIEW_LATER_SCORE" value={t.review_later} mono />
            <Row label="DEDUP_COSINE_THRESHOLD" value={t.dedup_cosine} mono />
            <Row label="DUPLICATE_FEATURE_THRESHOLD" value={t.duplicate_feature} mono />
          </tbody>
        </table>
      </div>

      {cost ? (
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Стоимость по провайдерам</h2>
          <table style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Провайдер</th>
                <th className="num">Запросов</th>
                <th className="num">Токенов вход</th>
                <th className="num">Токенов выход</th>
                <th className="num">USD</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries((cost as any).all_time?.providers || {}).map(
                ([provider, stats]: [string, any]) => (
                  <tr key={provider}>
                    <td className="mono">{provider}</td>
                    <td className="num">{stats.requests}</td>
                    <td className="num">{stats.prompt_tokens.toLocaleString("ru-RU")}</td>
                    <td className="num">{stats.completion_tokens.toLocaleString("ru-RU")}</td>
                    <td className="num">${stats.cost_usd.toFixed(4)}</td>
                  </tr>
                ),
              )}
              {Object.keys((cost as any).all_time?.providers || {}).length === 0 ? (
                <tr>
                  <td colSpan={5} style={{ color: "var(--tx-3)" }}>
                    Вызовов ещё не было.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}

function Row({ label, value, mono }: { label: string; value: any; mono?: boolean }) {
  return (
    <tr>
      <td style={{ color: "var(--tx-3)" }}>{label}</td>
      <td className={mono ? "mono num" : "num"} style={{ color: "var(--tx)" }}>
        {String(value ?? "—")}
      </td>
    </tr>
  );
}

function Flag({ label, ok, offNote }: { label: string; ok: boolean; offNote?: string }) {
  return (
    <tr>
      <td style={{ color: "var(--tx-3)" }}>{label}</td>
      <td className="num" style={{ color: ok ? "var(--recommended)" : "var(--tx-3)" }}>
        {ok ? "✓ настроено" : offNote || "не настроено"}
      </td>
    </tr>
  );
}
