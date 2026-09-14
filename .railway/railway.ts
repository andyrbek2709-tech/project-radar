/**
 * Railway Infrastructure as Code.
 *
 * Зачем этот файл, если рядом лежат railway.json:
 * Railway объявил Config-as-Code (railway.json / railway.toml) устаревшим —
 * существующие файлы работают до 01.12.2026, дальше только этот формат.
 * Разворачиваемся сегодня на railway.json, а здесь лежит готовая замена:
 * переключение — одна настройка в дашборде, без переписывания конфигов
 * в ноябре под дедлайн.
 *
 * ВАЖНО: API Infrastructure as Code у Railway новый и мог измениться.
 * Перед переключением сверься с docs.railway.com/config-as-code —
 * этот файл писался по документации на сентябрь 2026 и в бою не проверялся.
 */

const BACKEND_DOCKERFILE = "backend/Dockerfile";
const BACKEND_WATCH = ["backend/**"];

export default {
  services: {
    // ---------------------------------------------------------------- api
    api: {
      build: {
        builder: "DOCKERFILE",
        dockerfilePath: BACKEND_DOCKERFILE,
        watchPatterns: BACKEND_WATCH,
      },
      deploy: {
        // Миграции применяются перед стартом: единственный сервис,
        // которому это поручено, чтобы не было гонки.
        startCommand:
          "sh -c 'alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port $PORT'",
        healthcheckPath: "/health",
        healthcheckTimeout: 180,
        restartPolicyType: "ON_FAILURE",
        numReplicas: 1,
      },
    },

    // ------------------------------------------------------------- worker
    worker: {
      build: {
        builder: "DOCKERFILE",
        dockerfilePath: BACKEND_DOCKERFILE,
        watchPatterns: BACKEND_WATCH,
      },
      deploy: {
        startCommand:
          "celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2 --max-tasks-per-child=200",
        restartPolicyType: "ON_FAILURE",
        numReplicas: 1,
      },
    },

    // ---------------------------------------------------------- scheduler
    // Отдельно от worker: 'worker -B' на двух репликах = двойное расписание.
    scheduler: {
      build: {
        builder: "DOCKERFILE",
        dockerfilePath: BACKEND_DOCKERFILE,
        watchPatterns: BACKEND_WATCH,
      },
      deploy: {
        startCommand:
          "celery -A app.tasks.celery_app beat --loglevel=info --scheduler redbeat.RedBeatScheduler",
        restartPolicyType: "ON_FAILURE",
        numReplicas: 1,
      },
    },

    // ----------------------------------------------------------- telegram
    // Одно живое MTProto-соединение. Две реплики с одной сессией = риск бана.
    telegram: {
      build: {
        builder: "DOCKERFILE",
        dockerfilePath: BACKEND_DOCKERFILE,
        watchPatterns: BACKEND_WATCH,
      },
      deploy: {
        startCommand: "python -m app.collectors.telegram_runner",
        restartPolicyType: "ON_FAILURE",
        numReplicas: 1,
      },
    },

    // ---------------------------------------------------------------- web
    web: {
      build: {
        builder: "DOCKERFILE",
        dockerfilePath: "web/Dockerfile",
        watchPatterns: ["web/**"],
      },
      deploy: {
        startCommand: "node server.js",
        healthcheckPath: "/",
        healthcheckTimeout: 120,
        restartPolicyType: "ON_FAILURE",
        numReplicas: 1,
      },
    },
  },
};
