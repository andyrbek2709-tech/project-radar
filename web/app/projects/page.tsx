"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import ProfileEditor from "@/components/ProfileEditor";
import type { Project } from "@/lib/types";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ slug: "", name: "", github_repository: "" });

  async function load() {
    setLoading(true);
    try {
      setProjects(await api.projects());
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

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      await api.createProject({
        slug: form.slug.trim(),
        name: form.name.trim(),
        github_repository: form.github_repository.trim() || null,
        run_audit: true,
      });
      setForm({ slug: "", name: "", github_repository: "" });
      setToast("Проект создан. Если указан репозиторий — профиль собран автоматически.");
      setTimeout(() => setToast(null), 5000);
      load();
    } catch (err) {
      setToast(`Ошибка: ${(err as Error).message}`);
      setTimeout(() => setToast(null), 6000);
    } finally {
      setCreating(false);
    }
  }

  async function runAudit(id: string) {
    setToast("Техревизия запущена…");
    try {
      const result = await api.auditProject(id);
      setToast(
        result.status === "ok"
          ? `Ревизия завершена: ${result.features_detected} фич, ${result.search_keywords?.length ?? 0} ключевых слов`
          : `Ревизия не удалась: ${result.error}`,
      );
      load();
    } catch (err) {
      setToast(`Ошибка: ${(err as Error).message}`);
    }
    setTimeout(() => setToast(null), 6000);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Projects</h1>
          <div className="subtitle">
            Профиль проекта — эталон, относительно которого оценивается каждая находка
          </div>
        </div>
      </div>

      {error ? <div className="error-box">{error}</div> : null}

      <div className="card">
        <h2>Новый проект</h2>
        <div className="subtitle" style={{ marginBottom: 14 }}>
          Если указать репозиторий, система сама прочитает README, зависимости,
          docker-compose и структуру, и построит первичный search profile.
        </div>
        <form onSubmit={createProject} style={{ display: "grid", gap: 10 }}>
          <div className="grid grid-2" style={{ gap: 10 }}>
            <input
              type="text"
              placeholder="slug (например enghub)"
              value={form.slug}
              onChange={(e) => setForm({ ...form, slug: e.target.value })}
              required
              pattern="[a-z0-9][a-z0-9_\-]*"
            />
            <input
              type="text"
              placeholder="Название (EngHub)"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </div>
          <input
            type="text"
            placeholder="GitHub-репозиторий: owner/name (необязательно)"
            value={form.github_repository}
            onChange={(e) => setForm({ ...form, github_repository: e.target.value })}
          />
          <div>
            <button className="btn-primary" type="submit" disabled={creating}>
              {creating ? "Создаю и ревизую…" : "Создать проект"}
            </button>
          </div>
        </form>
      </div>

      {loading ? (
        <div className="skeleton" style={{ marginTop: 20 }} />
      ) : projects.length === 0 ? (
        <div className="empty" style={{ marginTop: 20 }}>
          <div className="empty-title">Проектов нет</div>
          Пока нет ни одного профиля, радару не с чем сопоставлять находки —
          pipeline не запустится.
        </div>
      ) : (
        <div style={{ marginTop: 20 }}>
          {projects.map((p) => (
            <div className="card" key={p.id}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
                <div style={{ minWidth: 0 }}>
                  <h2>{p.name}</h2>
                  <div className="finding-meta" style={{ marginTop: 6 }}>
                    <span className="chip">{p.slug}</span>
                    <span className="chip">{p.profile_source}</span>
                    {p.github_repository ? (
                      <span className="mono">{p.github_repository}</span>
                    ) : (
                      <span style={{ color: "var(--review)" }}>репозиторий не указан</span>
                    )}
                    {!p.is_active ? <span className="chip">выключен</span> : null}
                  </div>
                  {p.description ? (
                    <div className="field" style={{ marginTop: 10 }}>
                      {p.description}
                    </div>
                  ) : null}
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                  {p.github_repository ? (
                    <button className="btn-ghost btn-sm" onClick={() => runAudit(p.id)}>
                      Техревизия
                    </button>
                  ) : null}
                  <Link className="btn-ghost btn-sm" href={`/radar?project=${p.slug}`}>
                    Находки
                  </Link>
                </div>
              </div>

              {Object.keys(p.current_stack || {}).length > 0 ? (
                <div style={{ marginTop: 14 }}>
                  <span className="field-label">Текущий стек</span>
                  <div className="chips" style={{ marginTop: 4 }}>
                    {Object.entries(p.current_stack).flatMap(([area, items]) =>
                      (items as string[]).map((item) => (
                        <span className="chip" key={`${area}-${item}`}>
                          {item}
                        </span>
                      )),
                    )}
                  </div>
                </div>
              ) : null}

              {p.search_keywords?.length ? (
                <div style={{ marginTop: 12 }}>
                  <span className="field-label">Ключевые слова поиска</span>
                  <div className="chips" style={{ marginTop: 4 }}>
                    {p.search_keywords.map((k) => (
                      <span className="chip chip-accent" key={k}>
                        {k}
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="reason" style={{ fontStyle: "normal" }}>
                  Ключевых слов нет — GitHub Radar не знает, что искать для этого проекта.
                  Запусти техревизию или пропиши их вручную.
                </div>
              )}

              {p.things_not_needed?.length ? (
                <div style={{ marginTop: 12 }}>
                  <span className="field-label">Проекту не нужно</span>
                  <div className="chips" style={{ marginTop: 4 }}>
                    {p.things_not_needed.map((k) => (
                      <span className="chip" key={k}>
                        {k}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              <ProfileEditor project={p} onSaved={load} />
            </div>
          ))}
        </div>
      )}

      {toast ? <div className="toast">{toast}</div> : null}
    </>
  );
}
