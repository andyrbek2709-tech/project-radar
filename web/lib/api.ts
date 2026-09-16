/** Клиент API. Все запросы идут через /api/proxy — креды остаются на сервере. */
import type {
  Dashboard,
  DailyReport,
  Finding,
  FindingPage,
  Project,
  Source,
  TelegramStatus,
} from "./types";

const BASE = "/api/proxy";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      /* тело не JSON — оставляем код */
    }
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  dashboard: () => request<Dashboard>("/stats/dashboard"),

  /** Markdown-выгрузка находок для агента. Не JSON — поэтому мимо request(). */
  agentDigest: async (statuses = "RECOMMENDED,REVIEW_LATER", project?: string) => {
    const qs = new URLSearchParams({ status: statuses });
    if (project) qs.set("project", project);
    const res = await fetch(`${BASE}/findings/export/agent?${qs}`, { cache: "no-store" });
    if (!res.ok) throw new ApiError(`HTTP ${res.status}`, res.status);
    return res.text();
  },
  cost: () => request<Record<string, unknown>>("/stats/cost"),

  findings: (params: Record<string, string | number | undefined> = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
    }
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<FindingPage>(`/findings${suffix}`);
  },
  finding: (id: string) => request<Finding & Record<string, any>>(`/findings/${id}`),

  analysisContext: (id: string, projectId?: string) =>
    request<{ prompt: string; char_count: number; project_slug: string }>(
      `/findings/${id}/analysis-context${projectId ? `?project_id=${projectId}` : ""}`,
    ),

  overrideDecision: (
    id: string,
    body: { project_id?: string; status: string; reason: string; review_in_days?: number },
  ) => request(`/findings/${id}/decision`, { method: "POST", body: JSON.stringify(body) }),

  manualAnalysis: (id: string, body: { project_id: string; analysis: unknown }) =>
    request(`/findings/${id}/manual-analysis`, { method: "POST", body: JSON.stringify(body) }),

  reviewQueue: (dueOnly = false) =>
    request<any[]>(`/findings/queue/review-later${dueOnly ? "?due_only=true" : ""}`),

  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (body: unknown) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: string, body: unknown) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  auditProject: (id: string) => request<any>(`/projects/${id}/audit`, { method: "POST" }),
  addFeature: (id: string, body: unknown) =>
    request(`/projects/${id}/features`, { method: "POST", body: JSON.stringify(body) }),

  sources: (kind?: string) => request<Source[]>(`/sources${kind ? `?kind=${kind}` : ""}`),
  toggleSource: (id: string, isActive: boolean) =>
    request<Source>(`/sources/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_active: isActive }),
    }),
  resetCursor: (id: string) => request(`/sources/${id}/reset-cursor`, { method: "POST" }),

  telegramStatus: () => request<TelegramStatus>("/telegram/status"),

  reports: () => request<DailyReport[]>("/reports"),
  latestReport: () => request<DailyReport>("/reports/latest"),

  runtimeSettings: () => request<Record<string, any>>("/settings/runtime"),
  trigger: (job: string, force = false) =>
    request<{ status: string; job: string; task_id: string }>(
      `/trigger/${job}${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),
};
