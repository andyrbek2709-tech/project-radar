export type DecisionStatus =
  | "CRITICAL"
  | "RECOMMENDED"
  | "REVIEW_LATER"
  | "ARCHIVED"
  | "REJECTED";

export interface Repository {
  full_name: string;
  url: string;
  description: string | null;
  language: string | null;
  topics: string[];
  license_spdx: string | null;
  stars: number;
  forks: number;
  open_issues: number;
  contributors_count: number | null;
  commits_last_week: number | null;
  latest_release_tag: string | null;
  latest_release_at: string | null;
  pushed_at_gh: string | null;
  created_at_gh: string | null;
  archived: boolean;
}

export interface Match {
  project_id: string;
  project_slug: string | null;
  project_name: string | null;
  relevance_score: number;
  novelty_score: number;
  project_fit_score: number;
  improvement_score: number;
  maturity_score: number;
  activity_score: number;
  implementation_cost_score: number;
  risk_score: number;
  confidence_score: number;
  radar_score: number;
  score_breakdown: Record<string, unknown>;
  max_similarity_to_features: number | null;
  nearest_feature_name: string | null;
  categories: string[];
  why_relevant: string | null;
  what_we_have: string | null;
  what_it_offers: string | null;
  advantages: string[];
  disadvantages: string[];
  integration_complexity: string | null;
  recommend_deep_analysis: boolean;
}

export interface Decision {
  status: DecisionStatus;
  reason: string;
  reason_code: string | null;
  decided_by: string;
  radar_score_at_decision: number | null;
  created_at: string;
}

export interface Finding {
  id: string;
  kind: string;
  title: string;
  url: string | null;
  summary: string | null;
  found_via: string[];
  seen_count: number;
  status: string;
  is_noise: boolean;
  first_seen_at: string;
  last_seen_at: string;
  repository: Repository | null;
  stars_delta: number | null;
  match?: Match | null;
  decision?: Decision | null;
}

export interface FindingPage {
  items: Finding[];
  total: number;
  limit: number;
  offset: number;
}

export interface Project {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  business_purpose: string | null;
  existing_architecture: string | null;
  github_repository: string | null;
  current_stack: Record<string, string[]>;
  integrations: string[];
  current_problems: string[];
  planned_features: string[];
  technology_interests: string[];
  search_keywords: string[];
  negative_keywords: string[];
  things_not_needed: string[];
  priority_areas: string[];
  profile_source: string;
  profile_locked_fields: string[];
  is_active: boolean;
  features?: Feature[];
  findings_count?: number;
  critical_count?: number;
  recommended_count?: number;
}

export interface Feature {
  id: string;
  name: string;
  description: string | null;
  category: string | null;
  source: string;
}

export interface Source {
  id: string;
  kind: string;
  external_id: string;
  title: string | null;
  is_active: boolean;
  last_run_at: string | null;
  paused_until: string | null;
  last_error: string | null;
  last_item_id: string | null;
  items_collected: number;
}

export interface Dashboard {
  findings_total: number;
  findings_24h: number;
  by_status: Record<string, number>;
  by_project: { slug: string; name: string; total: number; avg_score: number }[];
  pending_raw_items: number;
  review_queue_due: number;
  sources: Record<string, number>;
  cost: {
    today: { total_cost_usd: number; providers: Record<string, any> };
    month: { total_cost_usd: number; providers: Record<string, any> };
    all_time: { total_cost_usd: number; providers: Record<string, any> };
    projected_month_usd: number;
    deep_analyses_today: number;
    deep_analyses_limit: number;
  };
  last_report: {
    date: string;
    is_empty: boolean;
    critical: number;
    recommended: number;
    review_later: number;
  } | null;
  collectors: {
    collector: string;
    status: string;
    started_at: string;
    finished_at: string | null;
    items_fetched: number;
    items_new: number;
    api_requests: number;
    rate_limit_remaining: number | null;
    error: string | null;
  }[];
}

export interface DailyReport {
  id: string;
  report_date: string;
  telegram_messages_processed: number;
  github_candidates: number;
  after_cheap_filter: number;
  after_ai_filter: number;
  deep_analyses_count: number;
  critical_count: number;
  recommended_count: number;
  review_later_count: number;
  rejected_count: number;
  duplicates_count: number;
  estimated_cost_usd: number;
  is_empty: boolean;
  body_markdown: string;
  created_at: string;
}
