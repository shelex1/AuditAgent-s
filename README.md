# AuditAgent-s — Совет ИИ-аудиторов для Claude Code

> **MCP-сервер, который собирает "совет" из 5 разных языковых моделей,
> проводит между ними трёхраундовые дебаты над вашим кодом и возвращает
> в Claude Code компактный вердикт и готовые патчи в формате unified-diff.**

`AuditAgent-s` превращает code review из монолога одной модели в
коллегиальное обсуждение пятью. Каждый участник играет свою роль
(параноик по безопасности, прагматичный инженер, критик, ревьюер
качества, рефакторер) — они спорят, уточняют факты, голосуют, и только
после этого вы получаете итог.

---

## Оглавление

- [Что это и зачем](#что-это-и-зачем)
- [Как это работает](#как-это-работает)
- [Возможности (MCP-инструменты)](#возможности-mcp-инструменты)
- [Провайдеры и модели](#провайдеры-и-модели)
- [Преимущества](#преимущества)
- [Требования](#требования)
- [Установка](#установка)
- [Настройка API-ключей](#настройка-api-ключей)
- [Настройка совета из 5 моделей](#настройка-совета-из-5-моделей)
- [Регистрация MCP в Claude Code](#регистрация-mcp-в-claude-code)
- [Проверка работоспособности](#проверка-работоспособности)
- [Примеры использования](#примеры-использования)
- [Структура проекта](#структура-проекта)
- [FAQ](#faq)

---

## Что это и зачем

Одна модель может ошибиться, упустить уязвимость, выдать галлюцинацию
или слишком уверенно защищать свой вариант. **Совет из пяти независимых
моделей** с разными ролями компенсирует слабости каждой:

- один — подозрительный параноик (ищет RCE, SQL-инъекции, утечки),
- другой — прагматик (думает о maintainability и стоимости),
- третий — адверсариальный критик (намеренно ищет дыры в чужих аргументах),
- четвёртый — следит за качеством кода и стилем,
- пятый — рефакторер (предлагает упрощения).

Каждый видит код, **все три раунда видят аргументы остальных**, и на
выходе вы получаете не одно мнение, а агрегированный вердикт с
голосованием.

Сервер выполнен как **MCP-сервер** (Model Context Protocol), поэтому
подключается к Claude Code одной строкой конфига — и Claude получает
пять новых инструментов: `consult_council`, `scan_project`,
`investigate_bug`, `get_debate_log`, `list_proposals`.

---

## Как это работает

```
┌─────────────────────────────────────────────────────────────┐
│                       Claude Code (CLI)                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ MCP stdio
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              anti-hacker (Python MCP server)                 │
│                                                              │
│   ┌──────────────┐   ┌────────────────┐   ┌──────────────┐   │
│   │ Cartographer │──▶│   Orchestra    │──▶│  Aggregator  │   │
│   │ (скоринг     │   │  (3 раунда     │   │  (вердикт +  │   │
│   │  файлов по   │   │   дебатов      │   │   unified-   │   │
│   │   риску)     │   │   × 5 моделей) │   │   diff)      │   │
│   └──────────────┘   └────────┬───────┘   └──────────────┘   │
│                               │                              │
└───────────────────────────────┼──────────────────────────────┘
                                │ HTTP (OpenAI-совместимый API)
                 ┌──────────────┼───────────────┐
                 ▼              ▼               ▼
          ┌──────────┐   ┌─────────────┐   ┌──────────────┐
          │OpenRouter│   │   Ollama    │   │  LiteLLM     │
          │ (free)   │   │  (локально, │   │  Proxy       │
          │          │   │   fallback) │   │  (опц.)      │
          └──────────┘   └─────────────┘   └──────────────┘
```

**Цепочка обработки одного запроса:**

1. **Cartographer** — быстрая лёгкая модель прочитывает карту
   репозитория и ранжирует файлы по риску.
2. **Orchestra** — для каждого из 5 участников совета запускаются
   3 раунда:
   - Раунд 1 — независимый первичный анализ.
   - Раунд 2 — каждый видит ответы остальных и может пересмотреть позицию.
   - Раунд 3 — финальный вердикт с голосованием.
3. **Fallback-цепочка** — если OpenRouter вернул `rate_limit` или
   `quota_exhausted` (включая характерный "200 OK с пустым телом"),
   вызов автоматически уходит к следующему провайдеру в цепочке
   (по умолчанию — Ollama).
4. **Aggregator** — собирает позиции, считает голоса, формирует
   unified-diff патч, который можно сразу применить через `git apply`.
5. **Debate log** — полный JSON-лог дебатов сохраняется в `debates/`
   с уникальным `debate_id` для последующего разбора.

---

## Возможности (MCP-инструменты)

После подключения к Claude Code становятся доступны **5 инструментов:**

| Инструмент | Назначение |
|---|---|
| `consult_council` | Трёхраундовые дебаты 5 моделей по конкретным файлам. Режимы: `review`, `security`, `refactor`, `free`. |
| `scan_project` | Картограф ранжирует проект по риску, затем глубокий ревью топ-N самых рискованных файлов. Фокусы: `security`, `quality`, `perf`, `all`. |
| `investigate_bug` | Дебаты с гипотезами для поиска корневой причины бага. Принимает симптом, стек-трейс, шаги воспроизведения. |
| `get_debate_log` | Полный JSON-лог дебатов по `debate_id` — все реплики всех раундов. |
| `list_proposals` | Список сохранённых `.patch`-файлов с метаданными, ожидающих ручного применения. |

---

## Провайдеры и модели

Архитектура поддерживает **три типа провайдеров** одновременно, каждый —
через OpenAI-совместимый API. Реализация провайдеров лежит в одном
универсальном клиенте `src/anti_hacker/openrouter/client.py` — меняется
только `base_url` и опциональный API-ключ.

### 1. OpenRouter — основной (бесплатные модели)

[openrouter.ai](https://openrouter.ai) — агрегатор, дающий доступ к
десяткам моделей через один API-ключ. Многие модели доступны с тегом
`:free` без оплаты.

- **Один ключ OpenRouter покрывает все 5 участников совета** — вам НЕ
  нужны 5 отдельных ключей. Разные модели выбираются в
  `config/council.toml`, а ключ читается из `OPENROUTER_API_KEY`.
- Примеры бесплатных моделей, которые можно использовать (проверяйте
  актуальность на [openrouter.ai/models](https://openrouter.ai/models),
  так как список меняется):
  - `z-ai/glm-4.5-air:free`
  - `openai/gpt-oss-120b:free`
  - `nvidia/nemotron-3-nano-30b-a3b:free`
  - `nvidia/nemotron-3-super-120b-a12b:free`
  - `nousresearch/hermes-3-llama-3.1-405b:free`
- Есть характерная особенность: при исчерпании квоты OpenRouter иногда
  возвращает `HTTP 200 OK` с **пустым телом**. У нас это распознаётся
  как ошибка `quota_exhausted` (флаг `empty_means_quota = true`) и
  автоматически активирует fallback.

### 2. Ollama — локальный fallback (опционально, бесплатно)

Ollama — локальная inference-платформа. Запускается через
[Ollama Desktop](https://ollama.com) и слушает на
`http://localhost:11434/v1` (OpenAI-совместимый endpoint).

- **API-ключ не нужен** — локальный сервер без аутентификации.
- Поддерживаются и обычные модели (`llama3.1`, `qwen2.5` и т.д.), и
  облачные модели через Ollama Cloud (например, `minimax-m2.7:cloud`).
- Реализация — тот же `OpenRouterClient` с другим `base_url`;
  отдельного модуля нет.

### 3. LiteLLM Proxy as a provider (опционально)

[LiteLLM](https://docs.litellm.ai) — универсальная прослойка, которая
нормализует 100+ LLM-провайдеров за **одним OpenAI-совместимым
endpoint'ом**. Нужен, если хочется собрать совет из нативных API,
которые AntiHacker напрямую не знает — Anthropic Claude, Google Gemini,
AWS Bedrock, Azure OpenAI, Vertex AI и т.д. Запускается как отдельный
прокси-сервер; AntiHacker ходит в него как в обычный OpenAI-endpoint,
так что **менять код не нужно**.

**Шаг 1.** Установить LiteLLM Proxy (отдельно от AntiHacker):

```bash
pip install 'litellm[proxy]'
```

**Шаг 2.** Создать `litellm.yaml` рядом с проектом. Пример с Anthropic,
Gemini и Bedrock:

```yaml
model_list:
  # Anthropic Claude (native API, без OpenRouter-посредника)
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: claude-opus
    litellm_params:
      model: anthropic/claude-opus-4-7
      api_key: os.environ/ANTHROPIC_API_KEY

  # Google Gemini
  - model_name: gemini-pro
    litellm_params:
      model: gemini/gemini-2.0-pro
      api_key: os.environ/GEMINI_API_KEY

  # AWS Bedrock (ключи берутся из стандартных AWS_* переменных)
  - model_name: bedrock-claude
    litellm_params:
      model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
      aws_region_name: us-east-1

general_settings:
  # master_key — это тот ключ, который AntiHacker будет слать в LiteLLM.
  # Любая непустая строка; он НЕ уходит в Anthropic/Gemini/Bedrock.
  master_key: sk-litellm-local
```

**Шаг 3.** Добавить ключи провайдеров и `LITELLM_API_KEY` в `.env`:

```env
LITELLM_API_KEY=sk-litellm-local
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
# Для Bedrock:
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

**Шаг 4.** Запустить прокси (оставить висеть в отдельном терминале):

```bash
litellm --config litellm.yaml --port 4000
```

**Шаг 5.** Зарегистрировать LiteLLM в `config/council.toml` как ещё
одного провайдера и назначить участнику. Поле `model` у члена совета
должно совпадать с `model_name` из `litellm.yaml`, **не** с оригинальным
ID модели:

```toml
[[providers]]
name = "litellm"
base_url = "http://localhost:4000/v1"
api_key_env = "LITELLM_API_KEY"

[[members]]
name = "claude-sonnet"
model = "claude-sonnet"          # ← это model_name из litellm.yaml
role = "security-paranoid"
timeout = 120
provider = "litellm"
fallbacks = [
  { provider = "ollama", model = "minimax-m2.7:cloud" },
]
```

Можно **смешивать** провайдеров: часть участников совета — на
OpenRouter (бесплатные модели), часть — через LiteLLM (платные native
API). Fallback'и работают одинаково.

### Цепочка fallback

Каждый участник совета в `council.toml` объявляет **упорядоченный список
fallback'ов**. Например:

```toml
[[members]]
name = "glm-4.5-air"
model = "z-ai/glm-4.5-air:free"
provider = "openrouter"
fallbacks = [
  { provider = "ollama", model = "minimax-m2.7:cloud" },
]
```

Если `openrouter` вернул `rate_limit` или `quota_exhausted`, совет
пробует `ollama`, и только если упал и он — участник пропускает раунд.

---

## Преимущества

- **Пять мнений вместо одного.** Меньше риск галлюцинации или слепого
  пятна у конкретной модели.
- **Три раунда дебатов.** Модели видят чужие аргументы и пересматривают
  свои позиции — итог обычно сильнее любого одиночного ответа.
- **Роли участников.** Каждая модель работает с уклоном в свою
  специализацию (security / pragmatism / criticism / quality /
  refactoring).
- **Готовые патчи.** На выходе — не пересказ в словах, а
  `unified-diff`, который применяется через `git apply`.
- **Бесплатно по умолчанию.** OpenRouter free tier + Ollama локально —
  можно запуститься вообще без единого платного ключа.
- **Прозрачность.** Каждый дебат сохраняется в `debates/<debate_id>.json`
  целиком — все реплики всех моделей всех раундов.
- **Fallback-цепочка.** Автоматический обход rate-limit и исчерпания
  квот без потери ответа.
- **Кеш.** Повторяющиеся запросы не тратят квоту (`cache_ttl_seconds`).
- **Полная изоляция секретов.** Ключи живут только в `.env`
  (gitignore), никуда не логируются, не попадают в `debates/`.

---

## Требования

- Python **3.11** или новее
- Git
- Аккаунт на [OpenRouter](https://openrouter.ai) (бесплатный)
- Claude Code CLI (чтобы подключить MCP-сервер)
- *Опционально:* Ollama Desktop для локального fallback
- *Опционально:* LiteLLM Proxy для доступа к Anthropic / Gemini / Bedrock /
  Azure (см. раздел «LiteLLM Proxy as a provider»)

---

## Установка

### 1. Клонируем репозиторий

```bash
git clone https://github.com/shelex1/AuditAgent-s.git
cd AuditAgent-s
```

### 2. Создаём виртуальное окружение

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 3. Ставим пакет

```bash
pip install -e .[dev]
```

Флаг `-e` ставит проект в editable-режиме; `[dev]` подтягивает
зависимости для тестов.

После установки в PATH появляется команда `anti-hacker` — именно её
будет запускать Claude Code.

---

## Настройка API-ключей

### Шаг 1. Скопировать шаблон

```bash
# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

### Шаг 2. Получить ключ OpenRouter (обязательно)

1. Зарегистрируйтесь на [openrouter.ai](https://openrouter.ai).
2. Откройте страницу [openrouter.ai/keys](https://openrouter.ai/keys).
3. Нажмите **Create Key**, скопируйте значение (начинается с `sk-or-v1-...`).
4. Вставьте его в `.env`:

```env
OPENROUTER_API_KEY=sk-or-v1-ваш-ключ-здесь
```

### Шаг 3. Настроить Ollama (опционально, бесплатно)

1. Установите [Ollama Desktop](https://ollama.com/download).
2. Запустите приложение — оно поднимет локальный сервер на
   `http://localhost:11434`.
3. Скачайте нужные модели, например:
   ```bash
   ollama pull llama3.1
   ollama pull qwen2.5
   ```
4. **Ключ не нужен** — Ollama читает запросы без аутентификации.

### Шаг 4. (опционально) LiteLLM Proxy для Anthropic / Gemini / Bedrock

Если хочется собрать совет из нативных API (не только OpenRouter) —
см. раздел «[LiteLLM Proxy as a provider](#3-litellm-proxy-as-a-provider-опционально)»:
там пошаговая настройка с готовым `litellm.yaml`.

Итоговый `.env` в минимальном варианте (только OpenRouter):

```env
OPENROUTER_API_KEY=sk-or-v1-...
```

---

## Настройка совета из 5 моделей

Файл `config/council.toml` описывает совет. Его не создаёт установщик —
нужно скопировать пример вручную:

```bash
# Windows
copy config\council.example.toml config\council.toml
# macOS / Linux
cp config/council.example.toml config/council.toml
```

Откройте `config/council.toml` и при желании замените модели на те,
которые вам доступны. Базовая структура:

```toml
# Реестр провайдеров — один ключ на провайдера
[[providers]]
name = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
empty_means_quota = true

[[providers]]
name = "ollama"
base_url = "http://localhost:11434/v1"
# api_key_env не указываем — Ollama без ключа

# 5 участников совета — каждый со своей ролью
[[members]]
name = "model-1"
model = "z-ai/glm-4.5-air:free"
role = "security-paranoid"
timeout = 120
provider = "openrouter"
fallbacks = [
  { provider = "ollama", model = "minimax-m2.7:cloud" },
]

# ... ещё 4 участника с ролями:
# pragmatic-engineer, adversarial-critic, code-quality, refactorer
```

**Важные моменты:**

- **Ровно 5 участников `[[members]]`.** Меньше/больше — ошибка конфига.
- **Пять разных ролей.** Каждая должна быть из списка:
  `security-paranoid`, `pragmatic-engineer`, `adversarial-critic`,
  `code-quality`, `refactorer`.
- **Желательно 5 разных моделей.** Одинаковые модели лишают совет
  разнообразия мнений — это главная фишка подхода.
- **Один ключ на провайдера, не на модель.** Все 5 участников совета на
  OpenRouter ходят через **один и тот же** `OPENROUTER_API_KEY`.
- Полный рабочий пример уже лежит в `config/council.example.toml`.

---

## Регистрация MCP в Claude Code

Добавьте сервер в MCP-конфиг Claude Code:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Linux:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "anti-hacker": {
      "command": "anti-hacker",
      "args": [],
      "env": {
        "ANTI_HACKER_PROJECT_ROOT": "C:\\path\\to\\your\\project"
      }
    }
  }
}
```

Либо через CLI Claude Code:

```bash
claude mcp add anti-hacker anti-hacker
```

Перезапустите Claude Code — в списке доступных MCP-инструментов
появятся `consult_council`, `scan_project`, `investigate_bug`,
`get_debate_log`, `list_proposals`.

---

## Проверка работоспособности

### Запустить тесты

```bash
pytest
```

Должно пройти ~180 тестов без сетевых обращений — все внешние вызовы
замоканы.

### Smoke-test живых моделей

Скрипт по очереди пингует каждую модель из вашего `council.toml`:

```bash
python scripts/smoke_test.py
```

Опциональные флаги:

```bash
# Дополнительно проверить Ollama (Ollama Desktop должен быть запущен)
python scripts/smoke_test.py --include-ollama
```

Вывод: `[OK]` или `[FAIL]` для каждой модели. Если видите `[FAIL]` с
`quota_exhausted` — попробуйте позже или замените модель на другую
`:free`.

---

## Примеры использования

### ВАЖНО: где запускать Claude Code

Аудит работает с файлами **той директории, в которой вы открыли
Claude Code**. Это значит:

1. Откройте терминал **в корне проекта, который хотите проверить**.
2. Запустите `claude` (или откройте проект в VS Code с расширением
   Claude Code) **из этой же директории**.
3. Только после этого вызывайте инструменты `anti-hacker`.

MCP-сервер берёт корень проекта из текущей рабочей директории Claude
Code (либо из переменной `ANTI_HACKER_PROJECT_ROOT`, если вы её
явно задали в конфиге MCP). Если запустить Claude Code из чужой
папки — совет будет анализировать чужие файлы либо вообще ничего не
найдёт.

```bash
# Правильно:
cd C:\path\to\my-project
claude
# → далее просите Claude: "проведи аудит..."

# Неправильно:
cd C:\
claude
# → MCP увидит корень диска, а не ваш проект
```

### Естественные промпты (так, как вы их обычно пишете)

Claude Code сам сопоставит просьбу с нужным инструментом — не нужно
знать названия `consult_council` и т. п. Достаточно сформулировать
задачу по-человечески:

```
Вызови аудит и проведи полный анализ кода на уязвимости и устрани их.
```

```
Используя аудит, проведи ревью и дебаг проекта.
```

```
Запусти аудит для файла src/auth/login.py — меня интересует
безопасность, найди уязвимости и предложи патч.
```

```
Просканируй весь проект на наличие security-проблем, покажи топ-20
самых рискованных файлов и разбери их подробно.
```

```
Через anti-hacker отреви только что изменённые файлы в этом коммите
и скажи, где я накосячил.
```

```
У меня баг: JWT-токен считается валидным после истечения срока.
Файлы src/auth/jwt.py и src/auth/middleware.py. Запусти расследование
через совет.
```

```
Покажи лог последних дебатов — хочу понять, почему модели согласились
на этот патч.
```

### Явные вызовы (если хочется контроля)

Если нужно точно указать инструмент и параметры:

```
Используй инструмент consult_council для файла src/auth/login.py,
режим security — найди потенциальные уязвимости.
```

```
Запусти scan_project с фокусом security и max_files=20 — хочу обзор
самых рискованных мест в проекте.
```

```
Инструмент investigate_bug: симптом — "JWT-токен считается валидным
после истечения срока". Файлы: src/auth/jwt.py, src/auth/middleware.py.
```

```
Покажи get_debate_log для debate_id abc123 — хочу увидеть все реплики.
```

### Что вы получите на выходе

- `consult_council` и `investigate_bug` — компактный JSON с вердиктом
  совета + ссылка на `.patch`-файл в `council_proposals/`.
- `scan_project` — отсортированный по риску список файлов с кратким
  описанием проблем каждой.
- Патч применяется обычным `git apply`:
  ```bash
  git apply council_proposals/<имя-патча>.patch
  ```
- Полный JSON-лог всех реплик всех раундов — в `debates/<debate_id>.json`.

---

## Структура проекта

```
AuditAgent-s/
├── src/anti_hacker/
│   ├── server.py              # MCP stdio-сервер, регистрирует 5 инструментов
│   ├── config.py              # Pydantic-схемы + загрузка council.toml и .env
│   ├── errors.py              # Типизированные ошибки (quota_exhausted и др.)
│   │
│   ├── openrouter/
│   │   └── client.py          # Универсальный OpenAI-совместимый HTTP-клиент
│   │                          # (используется для OpenRouter, Ollama, LiteLLM Proxy)
│   │
│   ├── council/
│   │   ├── orchestra.py       # Оркестратор 3-раундовых дебатов
│   │   ├── member.py          # Участник совета + логика fallback-цепочки
│   │   ├── aggregator.py      # Агрегация реплик → вердикт + патч
│   │   ├── prompts.py         # Системные промпты по ролям
│   │   └── cache.py           # TTL-кеш дебатов
│   │
│   ├── scanners/
│   │   ├── cartographer.py    # Скоринг файлов по риску
│   │   └── file_filter.py     # Фильтр .gitignore, бинарников и т.п.
│   │
│   ├── tools/
│   │   ├── consult.py         # Реализация consult_council
│   │   ├── scan.py            # Реализация scan_project
│   │   ├── investigate.py     # Реализация investigate_bug
│   │   └── logs.py            # get_debate_log + list_proposals
│   │
│   └── io/
│       ├── debate_log.py      # Персист JSON-логов дебатов
│       └── proposals.py       # Сохранение .patch-файлов
│
├── config/
│   ├── council.example.toml   # Пример конфига (в git)
│   └── council.toml           # Ваш локальный конфиг (gitignored)
│
├── scripts/
│   └── smoke_test.py          # Проверка всех моделей в council.toml
│
├── tests/                     # ~180 тестов, все с моками
├── docs/superpowers/          # Дизайн-документы и планы реализации
│
├── .env.example               # Шаблон переменных окружения
├── .env                       # Ваши ключи (gitignored)
├── pyproject.toml             # Пакет + entry-point `anti-hacker`
└── README.md                  # Этот файл
```

---

## FAQ

**В: Нужны ли мне 5 разных API-ключей?**
О: Нет. Достаточно **одного** ключа OpenRouter — он даёт доступ ко всем
5 моделям совета. Дополнительные ключи (Anthropic / Gemini / Bedrock
через LiteLLM Proxy) нужны только если вы хотите native API нужных
вендоров вместо OpenRouter.

**В: Можно ли вообще без платных провайдеров?**
О: Да. Минимальная конфигурация — один ключ OpenRouter на бесплатных
моделях. Для надёжности рекомендуется добавить локальный Ollama
(тоже бесплатно).

**В: Где отдельный модуль для Ollama?**
О: Его нет — Ollama использует тот же `OpenRouterClient` с другим
`base_url`. Это сознательный выбор: Ollama Desktop предоставляет
OpenAI-совместимый endpoint, поэтому дублировать клиент нет смысла.

**В: Что если OpenRouter вернёт пустой ответ?**
О: Это особенность free tier при исчерпании квоты. Флаг
`empty_means_quota = true` распознаёт это как ошибку `quota_exhausted`
и активирует fallback-цепочку автоматически.

**В: Где хранятся логи дебатов?**
О: В `debates/<debate_id>.json` (gitignored). Полный JSON со всеми
репликами всех раундов каждой модели.

**В: Куда уходят патчи?**
О: В `council_proposals/*.patch` (gitignored). Применяются вручную
через `git apply council_proposals/<файл>.patch`.

**В: Утекут ли мои ключи в логи?**
О: Нет. Ключи читаются из `.env` один раз на старте, хранятся в памяти
и **никогда** не пишутся в `debates/`, `council_proposals/` или
стандартный лог.

---

## Лицензия и вклад

Репозиторий публичный. Pull-request'ы приветствуются. При обнаружении
проблем — открывайте issue.
