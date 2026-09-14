"use client";

import { useState } from "react";
import type { Finding } from "@/lib/types";
import CopyContextButton from "./CopyContextButton";
import ScoreBar from "./ScoreBar";
import { statusLabel } from "@/lib/labels";

function fmtDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

/** «5 дней назад» — по этому решают живой проект или брошенный. */
function freshness(value: string | null | undefined): string | null {
  if (!value) return null;
  const days = Math.floor((Date.now() - new Date(value).getTime()) / 86_400_000);
  if (days < 0) return null;
  if (days === 0) return "коммит сегодня";
  if (days === 1) return "коммит вчера";
  if (days < 30) return `коммит ${days} дн. назад`;
  const months = Math.round(days / 30);
  if (months < 12) return `коммит ${months} мес. назад`;
  return `коммит больше года назад`;
}

function growth(delta: number | null): string | null {
  if (delta === null || delta === undefined || delta === 0) return null;
  return delta > 0 ? `+${delta} за 2 недели` : `${delta} за 2 недели`;
}

export default function FindingCard({ finding }: { finding: Finding }) {
  const [expanded, setExpanded] = useState(false);
  const match = finding.match;
  const decision = finding.decision;
  const repo = finding.repository;
  const status = decision?.status ?? "";

  return (
    <article className={`finding s-${status}`}>
      <div className="finding-head">
        <div style={{ minWidth: 0 }}>
          {/* Заголовок — имя репозитория. finding.title собирается как
              «owner/repo — <description с GitHub>», то есть английский
              рекламный слоган автора. Суть даёт русское саммари ниже. */}
          <div className="finding-title">
            {finding.url ? (
              <a href={finding.url} target="_blank" rel="noreferrer noopener">
                {repo?.full_name || finding.title}
              </a>
            ) : (
              repo?.full_name || finding.title
            )}
          </div>

          <div className="finding-meta">
            {status ? <span className={`badge badge-${status}`}>{statusLabel(status)}</span> : null}
            {match?.project_name ? (
              <>
                <span>для</span>
                <span className="chip chip-accent">{match.project_name}</span>
              </>
            ) : null}
            {repo ? (
              <>
                <span className="sep">·</span>
                <span className="mono">{repo.full_name}</span>
                <span className="sep">·</span>
                <span>★ {repo.stars.toLocaleString("ru-RU")}</span>
                {growth(finding.stars_delta) ? (
                  <span className="chip">{growth(finding.stars_delta)}</span>
                ) : null}
                {freshness(repo.pushed_at_gh) ? (
                  <>
                    <span className="sep">·</span>
                    <span>{freshness(repo.pushed_at_gh)}</span>
                  </>
                ) : null}
              </>
            ) : null}
            {repo?.license_spdx ? (
              <>
                <span className="sep">·</span>
                <span>{repo.license_spdx}</span>
              </>
            ) : null}
            <span className="sep">·</span>
            <span>найдено {fmtDate(finding.first_seen_at)}</span>
            {finding.found_via.length ? (
              <>
                <span className="sep">·</span>
                <span>через {finding.found_via.join(", ")}</span>
              </>
            ) : null}
            {finding.seen_count > 1 ? (
              <span className="chip">×{finding.seen_count} появлений</span>
            ) : null}
          </div>
        </div>

        {match ? (
          <div className="score-block">
            <div className="score-value">{match.radar_score.toFixed(2)}</div>
            <div className="score-caption">radar score</div>
          </div>
        ) : null}
      </div>

      <div className="finding-body">
        {finding.summary ? <div className="field">{finding.summary}</div> : null}

        {match ? (
          <div className="chips">
            {match.categories.map((c) => (
              <span className="chip" key={c}>
                {c}
              </span>
            ))}
            {match.integration_complexity ? (
              <span className="chip">внедрение: {match.integration_complexity}</span>
            ) : null}
            {repo?.language ? <span className="chip">{repo.language}</span> : null}
            {repo?.latest_release_tag ? (
              <span className="chip">релиз {repo.latest_release_tag}</span>
            ) : null}
            {repo?.contributors_count !== null && repo?.contributors_count !== undefined ? (
              <span className="chip">{repo.contributors_count} контрибьюторов</span>
            ) : null}
          </div>
        ) : null}
      </div>

      {decision?.reason ? <div className="reason">{decision.reason}</div> : null}

      {expanded && match ? (
        <div style={{ marginTop: 16 }}>
        {match?.why_relevant ? (
          <div className="field">
            <span className="field-label">Почему релевантно</span>
            {match.why_relevant}
          </div>
        ) : null}

        {match?.what_we_have ? (
          <div className="field">
            <span className="field-label">Что у нас уже есть</span>
            {match.what_we_have}
            {match.nearest_feature_name && match.max_similarity_to_features !== null ? (
              <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--tx-3)" }}>
                ближайшая фича: «{match.nearest_feature_name}» · похожесть{" "}
                {match.max_similarity_to_features.toFixed(2)}
              </div>
            ) : null}
          </div>
        ) : null}

        {match?.what_it_offers ? (
          <div className="field">
            <span className="field-label">Что предлагает нового</span>
            {match.what_it_offers}
          </div>
        ) : null}

        {match && (match.advantages.length > 0 || match.disadvantages.length > 0) ? (
          <div className="pros-cons">
            {match.advantages.length > 0 ? (
              <div>
                <span className="field-label">Преимущества</span>
                <ul className="list-tight">
                  {match.advantages.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {match.disadvantages.length > 0 ? (
              <div>
                <span className="field-label">Недостатки</span>
                <ul className="list-tight">
                  {match.disadvantages.map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

          <span className="field-label">Разбор оценки</span>
          <ScoreBar match={match} />
          <details className="raw">
            <summary>score_breakdown (сырой JSON)</summary>
            <pre>{JSON.stringify(match.score_breakdown, null, 2)}</pre>
          </details>
        </div>
      ) : null}

      <div className="finding-actions">
        <CopyContextButton findingId={finding.id} projectId={match?.project_id} />
        {match ? (
          <button className="btn-ghost btn-sm" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Свернуть разбор" : "Разбор и метрики"}
          </button>
        ) : null}
        {finding.url ? (
          <a
            className="btn-ghost btn-sm"
            href={finding.url}
            target="_blank"
            rel="noreferrer noopener"
          >
            Открыть ↗
          </a>
        ) : null}
        {match?.recommend_deep_analysis ? (
          <span className="chip chip-accent">рекомендован глубокий разбор</span>
        ) : null}
      </div>
    </article>
  );
}
