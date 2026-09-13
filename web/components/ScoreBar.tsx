import type { Match } from "@/lib/types";

function Metric({
  label,
  value,
  inverse = false,
}: {
  label: string;
  value: number;
  inverse?: boolean;
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="metric">
      <div className="metric-top">
        <span>{label}</span>
        <b>{value.toFixed(2)}</b>
      </div>
      <div className={`bar${inverse ? " inverse" : ""}`}>
        <span style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/**
 * Независимые метрики. Звёзды здесь отсутствуют осознанно: они входят
 * только в maturity и activity, и с логарифмическим насыщением.
 */
export default function ScoreBar({ match }: { match: Match }) {
  return (
    <div className="metrics">
      <Metric label="relevance" value={match.relevance_score} />
      <Metric label="project fit" value={match.project_fit_score} />
      <Metric label="improvement" value={match.improvement_score} />
      <Metric label="novelty" value={match.novelty_score} />
      <Metric label="maturity" value={match.maturity_score} />
      <Metric label="activity" value={match.activity_score} />
      <Metric label="cost ↑ хуже" value={match.implementation_cost_score} inverse />
      <Metric label="risk ↑ хуже" value={match.risk_score} inverse />
      <Metric label="confidence" value={match.confidence_score} />
    </div>
  );
}
