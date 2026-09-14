"use client";

/**
 * Ручное редактирование профиля проекта.
 *
 * Профиль — эталон, относительно которого оценивается каждая находка, и до сих
 * пор заполнить его можно было только техревизией репозитория или PATCH'ем
 * руками. На Railway шелла нет, так что проект без публичного репозитория
 * оставался с пустым профилем навсегда — при том что страница сама предлагала
 * «пропиши ключевые слова вручную».
 *
 * Отправляем ТОЛЬКО изменённые поля: PATCH заносит каждое присланное поле в
 * profile_locked_fields, и техревизия его больше не перезапишет. Отправить всё
 * скопом значит молча заморозить весь профиль.
 */
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";

const TEXT_FIELDS = [
  ["description", "Чем занимается проект", "Инженерная документация и работа с нормативами"],
  ["business_purpose", "Зачем он нужен", "Кому и какую задачу решает"],
  ["existing_architecture", "Как устроен сейчас", "FastAPI + Postgres, парсинг PDF через pdfplumber…"],
] as const;

const LIST_FIELDS = [
  ["search_keywords", "Ключевые слова поиска", "rag, pdf extraction, ocr", "Ими GitHub Radar ищет кандидатов."],
  ["priority_areas", "Приоритетные направления", "RAG, Document AI, парсинг PDF", "Что сейчас важнее всего."],
  ["current_problems", "Текущие проблемы", "медленный парсинг таблиц", "Находка, решающая их, получит выше оценку."],
  ["planned_features", "Что планируется", "поиск по нормативам", "Чтобы радар ловил заранее."],
  ["negative_keywords", "Стоп-слова", "crypto, nft, trading bot", "Режутся дешёвым фильтром до LLM."],
  ["things_not_needed", "Проекту не нужно", "своя очередь задач", "Идёт в промпт как явный отказ."],
] as const;

type TextField = (typeof TEXT_FIELDS)[number][0];
type ListField = (typeof LIST_FIELDS)[number][0];

/** «a, b\nc» → ["a","b","c"]. Пустые куски выбрасываем. */
function parseList(raw: string): string[] {
  return raw
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function initialState(project: Project) {
  const state: Record<string, string> = {};
  for (const [key] of TEXT_FIELDS) state[key] = project[key] ?? "";
  for (const [key] of LIST_FIELDS) state[key] = (project[key] ?? []).join(", ");
  return state;
}

/** Отпечаток серверных значений: по нему видно, что профиль перезаписали снаружи. */
function signatureOf(project: Project): string {
  return JSON.stringify(Object.values(initialState(project)));
}

export default function ProfileEditor({
  project,
  onSaved,
}: {
  project: Project;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<Record<string, string>>(() => initialState(project));
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  // Состояние формы инициализируется один раз, а профиль меняется снаружи:
  // техревизия переписывает поля на сервере, и без пересинхронизации форма
  // продолжала бы показывать старое — вплоть до сохранения устаревших значений
  // поверх свежих. Сверяемся с отпечатком серверных значений: перечитываем
  // форму, только когда изменился он, а не когда пользователь просто печатает.
  const [syncedFrom, setSyncedFrom] = useState(() => signatureOf(project));
  const signature = signatureOf(project);
  if (signature !== syncedFrom) {
    setSyncedFrom(signature);
    setForm(initialState(project));
  }

  // Дифф считаем на каждый рендер: он же решает, показывать ли кнопку активной.
  const changes = useMemo(() => {
    const diff: Record<string, string | string[]> = {};
    for (const [key] of TEXT_FIELDS) {
      const next = form[key].trim();
      if (next !== (project[key as TextField] ?? "")) diff[key] = next;
    }
    for (const [key] of LIST_FIELDS) {
      const next = parseList(form[key]);
      if (!sameList(next, project[key as ListField] ?? [])) diff[key] = next;
    }
    return diff;
  }, [form, project]);

  const changedCount = Object.keys(changes).length;

  async function save() {
    if (!changedCount) return;
    setSaving(true);
    setStatus(null);
    try {
      await api.updateProject(project.id, changes);
      setStatus(`Сохранено полей: ${changedCount}. Профиль учтётся со следующего прогона.`);
      onSaved();
    } catch (err) {
      setStatus(`Ошибка: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  const locked = new Set(project.profile_locked_fields || []);

  return (
    <details style={{ marginTop: 14 }}>
      <summary>Редактировать профиль</summary>

      <div className="subtitle" style={{ margin: "10px 0 14px" }}>
        Отправляются только изменённые поля. Каждое сохранённое поле техревизия
        больше не перезаписывает — правка руками всегда старше автопрофиля.
      </div>

      <div style={{ display: "grid", gap: 14 }}>
        {TEXT_FIELDS.map(([key, label, placeholder]) => (
          <div key={key}>
            <span className="field-label">
              {label}
              {locked.has(key) ? <span className="chip" style={{ marginLeft: 8 }}>правилось руками</span> : null}
            </span>
            <textarea
              rows={2}
              placeholder={placeholder}
              value={form[key]}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              style={{ marginTop: 4 }}
            />
          </div>
        ))}

        {LIST_FIELDS.map(([key, label, placeholder, hint]) => (
          <div key={key}>
            <span className="field-label">
              {label}
              {locked.has(key) ? <span className="chip" style={{ marginLeft: 8 }}>правилось руками</span> : null}
            </span>
            <input
              type="text"
              placeholder={placeholder}
              value={form[key]}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              style={{ marginTop: 4 }}
            />
            <div className="stat-note" style={{ marginTop: 4 }}>
              Через запятую. {hint}
            </div>
          </div>
        ))}

        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn-primary" type="button" onClick={save} disabled={saving || !changedCount}>
            {saving ? "Сохраняю…" : changedCount ? `Сохранить (${changedCount})` : "Изменений нет"}
          </button>
          {status ? <span className="stat-note">{status}</span> : null}
        </div>
      </div>
    </details>
  );
}
