/**
 * Скопируй в linear-config.local.js и заполни своими значениями.
 * Файл linear-config.local.js в .gitignore — не попадёт в git.
 *
 *   cp scripts/linear-config.local.example.js scripts/linear-config.local.js
 */

module.exports = {
  /** Personal API key из Linear → Settings → API */
  LINEAR_API_KEY: "lin_api_ВСТАВЬ_КЛЮЧ_ИЗ_LINEAR_SETTINGS",

  /**
   * Либо UUID команды (предпочтительно), либо короткий ключ команды (например DEV).
   * Если задан LINEAR_TEAM_ID — TEAM_KEY не используется.
   */
  LINEAR_TEAM_ID: "", // например "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  TEAM_KEY: "DEV",

  /**
   * Зависимости видны в списке/доске и фильтруются по лейблу (не только в описании).
   * Поставь true и перезапусти импорт для нужных JSON.
   */
  AUTO_DEPENDENCY_LABELS: false,
  // LABEL_DEPENDS_ON: "depends-on",

  /** Лейбл на тикетах, которые блокируют других (на них ссылается dependsOn). */
  AUTO_BLOCKER_LABELS: false,
  // LABEL_BLOCKS_OTHERS: "blocks-others",
};
