"use client";

import { useState } from "react";
import type { Finding } from "@/lib/types";
import CopyContextButton from "./CopyContextButton";
import ScoreBar from "./ScoreBar";

function fmtDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
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
          <div className="finding-title">
            {finding.url ? (
              <a href={finding.url} target="_blank" rel="noreferrer noopener">
                {finding.title}
              </a>
            ) : (
              finding.title
            )}
          </div>

          <div className="finding-meta">
            {status ? <span className={`badge badge-${status}`}>{status.replace("_", " ")}</span> : null}
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
            {expanded ? "Скрыть метрики" : "Метрики и разбор"}
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
