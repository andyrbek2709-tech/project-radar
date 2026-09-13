# =============================================================================
# Инициализация репозитория и первый push.
#
# Запусти из PowerShell в папке D:\radar:
#     .\push.ps1
#
# Коммиты делаются осмысленными порциями, а не одним гигантским — чтобы
# историю можно было читать и откатывать по частям.
#
# Если Git попросит авторизацию — либо `gh auth login`, либо Personal Access
# Token вместо пароля (GitHub не принимает пароль аккаунта с 2021 года).
# =============================================================================

$ErrorActionPreference = "Stop"
$Remote = "https://github.com/andyrbek2709-tech/project-radar.git"

function Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Commit($message, [string[]]$paths) {
    $existing = $paths | Where-Object { Test-Path $_ }
    if (-not $existing) {
        Write-Host "    пропускаю (нет файлов): $message" -ForegroundColor DarkGray
        return
    }
    git add -- $existing
    $staged = git diff --cached --name-only
    if (-not $staged) {
        Write-Host "    пропускаю (нет изменений): $message" -ForegroundColor DarkGray
        return
    }
    git commit -q -m $message
    Write-Host "    ✓ $message" -ForegroundColor Green
}

# --- проверки ----------------------------------------------------------------
Step "Проверяю git"
try {
    $version = git --version
    Write-Host "    $version"
} catch {
    Write-Host "Git не найден в PATH. Установи: https://git-scm.com/download/win" -ForegroundColor Red
    exit 1
}

Set-Location -Path $PSScriptRoot

# --- инициализация -----------------------------------------------------------
if (-not (Test-Path ".git")) {
    Step "Инициализирую репозиторий"
    git init -q
    git branch -M main
} else {
    Step "Репозиторий уже инициализирован"
}

if (-not (git config user.email)) {
    git config user.email "andreyfuture27@gmail.com"
    git config user.name  "andyrbek2709-tech"
    Write-Host "    настроил user.email / user.name локально для этого репозитория"
}

# --- защита от утечки секретов ----------------------------------------------
Step "Проверяю, что .env не попадёт в коммит"
if (Test-Path ".env") {
    $ignored = git check-ignore .env 2>$null
    if (-not $ignored) {
        Write-Host "ОСТАНОВКА: .env существует и НЕ игнорируется." -ForegroundColor Red
        Write-Host "Проверь .gitignore перед push — в .env лежат ключи и сессия Telegram." -ForegroundColor Red
        exit 1
    }
    Write-Host "    ✓ .env игнорируется"
} else {
    Write-Host "    .env отсутствует — нечего защищать"
}

# --- коммиты по слоям --------------------------------------------------------
Step "Формирую коммиты"

Commit "chore: gitignore, .env.example, конфиги Railway" @(
    ".gitignore", ".env.example", "railway.json", "railway.worker.json",
    "railway.scheduler.json", "railway.telegram.json", "railway.web.json",
    ".railway/railway.ts", "push.ps1", "Makefile"
)

Commit "docs: архитектура и схема данных" @(
    "docs/ARCHITECTURE.md", "docs/DATA_MODEL.md", "DECISIONS_RADAR.html"
)

Commit "feat(core): конфиг, структурированные логи с редактором секретов, движки БД" @(
    "backend/app/__init__.py", "backend/app/core"
)

Commit "feat(models): схема данных — проекты, источники, репозитории, находки, решения" @(
    "backend/app/models"
)

Commit "feat(db): alembic и начальная миграция с pgvector и HNSW-индексами" @(
    "backend/alembic", "backend/alembic.ini"
)

Commit "feat(analysis): эмбеддинги, клиенты Groq/OpenAI, схемы ответов, промпты" @(
    "backend/app/analysis"
)

Commit "feat(collectors): GitHub Radar с ETag-кэшем и Telegram на Telethon" @(
    "backend/app/collectors"
)

Commit "feat(services): нормализация, дедупликация, скоринг, движок решений, pipeline" @(
    "backend/app/services"
)

Commit "feat(api): FastAPI — проекты, радар, COPY ANALYSIS CONTEXT, статистика" @(
    "backend/app/api", "backend/app/schemas.py", "backend/app/main.py"
)

Commit "feat(tasks): Celery — расписание на redbeat и задачи радара" @(
    "backend/app/tasks"
)

Commit "feat(scripts): генерация Telegram-сессии, резолв id, посев проектов" @(
    "backend/scripts"
)

Commit "test: нормализация, скоринг, решения, редактирование секретов в логах" @(
    "backend/tests"
)

Commit "build: Dockerfile и зависимости backend" @(
    "backend/Dockerfile", "backend/.dockerignore",
    "backend/requirements.txt", "backend/requirements-dev.txt",
    "backend/pyproject.toml"
)

Commit "feat(web): Next.js — dashboard, radar, projects, sources, reports, settings" @(
    "web"
)

Commit "build: docker-compose для локального стенда" @(
    "docker-compose.yml"
)

Commit "docs: README с инструкцией деплоя и принятыми решениями" @(
    "README.md"
)

# Всё, что осталось незакоммиченным.
git add -A
$rest = git diff --cached --name-only
if ($rest) {
    git commit -q -m "chore: остальные файлы проекта"
    Write-Host "    ✓ остальные файлы" -ForegroundColor Green
}

# --- remote и push -----------------------------------------------------------
Step "Настраиваю remote"
$existingRemote = git remote 2>$null
if ($existingRemote -contains "origin") {
    git remote set-url origin $Remote
} else {
    git remote add origin $Remote
}
Write-Host "    origin → $Remote"

Step "Пушу в main"
Write-Host "    Если попросит логин — используй Personal Access Token вместо пароля." -ForegroundColor Yellow

git push -u origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "    Обычный push отклонён — похоже, на GitHub уже есть коммит" -ForegroundColor Yellow
    Write-Host "    (автоматический README при создании репозитория)." -ForegroundColor Yellow
    Write-Host "    Пробую вобрать его историю и запушить поверх…" -ForegroundColor Yellow

    git fetch origin main
    git rebase origin/main 2>$null
    if ($LASTEXITCODE -ne 0) {
        # Истории не связаны — наш README полнее автосгенерированного,
        # забираем свою версию.
        git rebase --abort 2>$null
        git push -u origin main --force
    } else {
        git push -u origin main
    }
}

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Push не прошёл. Скорее всего дело в авторизации." -ForegroundColor Red
    Write-Host "Варианты:" -ForegroundColor Red
    Write-Host "  gh auth login        (если установлен GitHub CLI)"
    Write-Host "  либо Personal Access Token вместо пароля:"
    Write-Host "  https://github.com/settings/tokens  → scope 'repo'"
    exit 1
}

Step "Готово"
Write-Host "https://github.com/andyrbek2709-tech/project-radar" -ForegroundColor Green
Write-Host ""
Write-Host "Дальше:" -ForegroundColor Cyan
Write-Host "  1. cp .env.example .env  и заполнить GITHUB_TOKEN, GROQ_API_KEY, OPENAI_API_KEY"
Write-Host "  2. docker compose up -d --build"
Write-Host "  3. открыть http://localhost:8000/health/deep — нужен pgvector: true"
Write-Host "  4. docker compose exec api python scripts/seed_projects.py"
