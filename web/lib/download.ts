/** Скачивание Markdown-выгрузки находок для агента.
 *
 * Файл забирается запросом и сохраняется из браузера, а не отдаётся редиректом:
 * прокси Next не пробрасывает Content-Disposition, а стучаться в бэкенд напрямую
 * из браузера нельзя — там Basic-авторизация.
 */
import { api } from "./api";

/** Что выгружать, если статус на странице не выбран: «все» — это шум из 425 отклонённых. */
export const DEFAULT_EXPORT_STATUSES = "RECOMMENDED,REVIEW_LATER";

export async function downloadAgentDigest(
  statuses: string = DEFAULT_EXPORT_STATUSES,
  project?: string,
): Promise<void> {
  const text = await api.agentDigest(statuses || DEFAULT_EXPORT_STATUSES, project);
  const stamp = new Date().toISOString().slice(0, 10);
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `radar-${stamp}.md`;
  link.click();
  URL.revokeObjectURL(url);
}
