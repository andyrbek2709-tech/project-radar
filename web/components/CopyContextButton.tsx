"use client";

import { useState } from "react";
import { api } from "@/lib/api";

/**
 * СКОПИРОВАТЬ КОНТЕКСТ — один клик, готовый промпт для ChatGPT/Codex.
 *
 * Промпт собирается на бэкенде: контекст (профиль проекта, существующие фичи,
 * метрики репозитория) живёт в базе, дублировать его в браузер незачем.
 */
export default function CopyContextButton({
  findingId,
  projectId,
  label = "СКОПИРОВАТЬ КОНТЕКСТ",
}: {
  findingId: string;
  projectId?: string;
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  async function handleCopy() {
    setState("loading");
    try {
      const { prompt, char_count } = await api.analysisContext(findingId, projectId);

      let copied = false;
      try {
        await navigator.clipboard.writeText(prompt);
        copied = true;
      } catch {
        // clipboard API недоступен вне https — откатываемся на textarea
        const ta = document.createElement("textarea");
        ta.value = prompt;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        copied = document.execCommand("copy");
        document.body.removeChild(ta);
      }

      if (!copied) throw new Error("не удалось записать в буфер");

      setState("done");
      setMessage(`Скопировано · ${char_count.toLocaleString("ru-RU")} символов`);
      setTimeout(() => setState("idle"), 2600);
    } catch (err) {
      setState("error");
      setMessage((err as Error).message);
      setTimeout(() => setState("idle"), 4000);
    }
  }

  return (
    <>
      <button
        className={state === "done" ? "btn-primary btn-sm" : "btn-ghost btn-sm"}
        onClick={handleCopy}
        disabled={state === "loading"}
      >
        {state === "loading" ? "Собираю…" : state === "done" ? "✓ Скопировано" : label}
      </button>
      {state !== "idle" && state !== "loading" && message ? (
        <div className="toast">{message}</div>
      ) : null}
    </>
  );
}
