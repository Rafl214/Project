# Запуск сервиса

## Что сейчас умеет сервис

- Flask backend с API для загрузки двух PDF-файлов.
- Встроенный frontend на `Project/templates/index.html`.
- Асинхронная обработка задач через `ThreadPoolExecutor`.
- Базовые настройки из `.env`.
- Клиентские override-настройки через JSON-файлы в `Project/client_configs/`.
- Опциональный режим запуска без frontend через `ENABLE_FRONTEND=0`.

## Как определяется клиент

Сервис пытается получить `client_id` в таком порядке:

1. заголовок `X-Client-ID`;
2. поле формы `client_id`;
3. query-параметр `client_id`.

Если отдельный конфиг для клиента не найден, используются базовые настройки из `.env` и `Project/Prompts.py`.

## Что можно переопределять для клиента

Для каждого клиента можно задать:

- `POLZA_API_KEY`
- `POLZA_BASE_URL`
- `MODEL_NAME`
- текст пользовательского запроса к модели
- системный промпт
- настройки reasoning
- лимит суммарного размера двух PDF для этого клиента

Глобальные настройки процесса вроде `CHECKER_THREADS`, `PORT`, `WEB_HOST`, `FLASK_DEBUG` и `ENABLE_FRONTEND` остаются общими для всего сервера.

## Быстрый запуск на ноутбуке

### 1. Установить Python и создать окружение

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Подготовить `.env`

Скопируйте пример:

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Linux/macOS:

```bash
cp .env.example .env
```

Минимально нужно заполнить:

```env
POLZA_API_KEY=your_real_key
POLZA_BASE_URL=https://polza.ai/api/v1
MODEL_NAME=openai/gpt-5.4
CHECKER_THREADS=4
MAX_CONTENT_LENGTH_MB=30
SERVER_MAX_CONTENT_LENGTH_MB=30
DEFAULT_REQUEST_INTRO_TEXT=Проанализируй эти документы:
DEFAULT_REASONING_ENABLED=1
DEFAULT_REASONING_EFFORT=low
CLIENT_ID_HEADER=X-Client-ID
CLIENT_CONFIGS_DIR=Project/client_configs
ENABLE_FRONTEND=1
PORT=5000
WEB_HOST=0.0.0.0
FLASK_DEBUG=0
```

Пояснение по лимитам:

- `MAX_CONTENT_LENGTH_MB` - базовый клиентский лимит.
- `SERVER_MAX_CONTENT_LENGTH_MB` - верхний предел на уровне Flask.

Если какому-то клиенту нужен лимит 50 МБ, то:

- в его JSON-конфиге ставьте `max_content_length_mb: 50`;
- в `.env` задайте `SERVER_MAX_CONTENT_LENGTH_MB=50` или выше.

### 3. Запустить сервис с frontend

```powershell
.\.venv\Scripts\python.exe Project\app.py
```

После старта сервис будет доступен:

- локально: `http://127.0.0.1:5000`
- по локальной сети: `http://<IP_ноутбука>:5000`

### 4. Запустить сервис без frontend

В `.env`:

```env
ENABLE_FRONTEND=0
```

После этого корневой маршрут `/` будет возвращать JSON с краткой справкой по API, а сам backend продолжит работать.

### 5. Проверить, что сервис поднялся

Откройте:

- `http://127.0.0.1:5000/healthz`

В ответе будут:

- состояние сервиса;
- включён ли frontend;
- список зарегистрированных клиентов;
- базовая конфигурация;
- путь до папки с клиентскими конфигами.

## Как добавить клиента

1. Откройте [Project/client_configs/README.md](Project/client_configs/README.md).
2. Для быстрого теста используйте уже готовый конфиг `Project/client_configs/demo-school.json`.
3. Для нового клиента скопируйте пример `Project/client_configs/examples/demo-school.json` в `Project/client_configs/<client_id>.json`.
4. При необходимости отредактируйте prompt-файл в `Project/client_configs/prompts/`

Пример:

```json
{
  "display_name": "Демо-клиент",
  "model_name": "openai/gpt-5.4",
  "request_intro_text": "Проверь эти документы по правилам демо-клиента и верни JSON-результат.",
  "system_prompt_file": "prompts/demo-school_system_prompt.txt",
  "reasoning_enabled": true,
  "reasoning_effort": "medium",
  "max_content_length_mb": 30
}
```

После этого можно:

- указать `client_id=demo-school` в форме frontend;
- или передать заголовок `X-Client-ID: demo-school` в API-запросе.

## Пример API-запроса

Windows PowerShell:

```powershell
curl.exe -X POST "http://127.0.0.1:5000/upload" `
  -H "X-Client-ID: demo-school" `
  -F "file1=@C:\path\criteria.pdf" `
  -F "file2=@C:\path\solution.pdf"
```

Пример ответа:

```json
{
  "job_id": "abc123",
  "status": "queued",
  "message": "Файлы приняты. Проверка запущена в фоне.",
  "status_url": "/result/abc123",
  "client": {
    "requested_client_id": "demo-school",
    "effective_client_id": "demo-school",
    "display_name": "Демо-клиент",
    "config_source": "Project/client_configs/demo-school.json",
    "model_name": "openai/gpt-5.4",
    "polza_base_url": "https://polza.ai/api/v1",
    "reasoning_enabled": true,
    "reasoning_effort": "medium",
    "max_content_length_mb": 30,
    "has_api_key": true
  }
}
```

Дальше результат можно забирать по `GET /result/<job_id>`.

## Как открыть доступ по локальной сети

1. В `.env` оставьте:

```env
WEB_HOST=0.0.0.0
PORT=5000
```

2. Узнайте IP ноутбука:

```powershell
ipconfig
```

3. При необходимости откройте порт в Windows Firewall:

```powershell
New-NetFirewallRule -DisplayName "Olympiad Checker 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
```

После этого сервис будет доступен с других устройств в той же сети по адресу вида:

`http://192.168.1.25:5000`

## Как запустить в фоне

### Вариант 1. Отдельное окно PowerShell

Самый простой способ для тестового стенда:

```powershell
.\.venv\Scripts\python.exe Project\app.py
```

И просто не закрывать окно.

### Вариант 2. PowerShell в фоне

```powershell
Start-Process -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList "Project\app.py" `
  -RedirectStandardOutput "server.out.log" `
  -RedirectStandardError "server.err.log"
```

### Вариант 3. Gunicorn

Подходит для Linux, WSL, VPS и хостингов:

```bash
gunicorn --chdir Project app:app --workers 1 --threads 8 --timeout 300 --bind 0.0.0.0:5000
```

## Docker

Если удобнее запускать в контейнере:

```bash
docker build -t olympiad-checker .
docker run --env-file .env -p 5000:5000 olympiad-checker
```

## Что важно помнить

- Очередь задач и статусы хранятся в памяти процесса.
- Если перезапустить приложение, незавершённые задачи и статусы пропадут.
- Для этапа тестирования на одном ноутбуке это нормально.
- Для следующего этапа развития лучше вынести очередь и хранение статусов в Redis или БД.
