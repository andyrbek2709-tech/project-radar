/**
 * Русские подписи для служебных значений API.
 *
 * Коды статусов (CRITICAL, REVIEW_LATER, …) — это значения из базы: они ездят
 * в query-строке, попадают в decisions.status и в отчёты. Переводить их нельзя,
 * переводится только то, что видит человек. Поэтому один словарь на всё
 * приложение: иначе подписи в меню, в фильтре и на бейдже карточки разъезжаются.
 */

export const STATUS_LABELS: Record<string, string> = {
  CRITICAL: "Критично",
  RECOMMENDED: "Рекомендовано",
  REVIEW_LATER: "Отложено",
  ARCHIVED: "Архив",
  REJECTED: "Отклонено",
};

/** Заголовок раздела радара. Для неизвестного кода — сам код, а не пустота. */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Радар";
  return STATUS_LABELS[status] ?? status.replace(/_/g, " ");
}

/** Порядок разделов радара — от срочного к отвергнутому. */
export const STATUS_ORDER = [
  "CRITICAL",
  "RECOMMENDED",
  "REVIEW_LATER",
  "ARCHIVED",
  "REJECTED",
] as const;
