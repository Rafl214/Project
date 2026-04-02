# Что добавлено

## 1) Многопоточность
- Проверка теперь запускается в фоне через `ThreadPoolExecutor`.
- Маршрут `/upload` быстро возвращает `job_id`, а страница опрашивает `/result/<job_id>`.
- Количество параллельных задач задаётся через `CHECKER_THREADS`.

## 2) Доступ к сайту по интернету
- Локальный запуск теперь слушает `0.0.0.0`, а не только `127.0.0.1`.
- Добавлены `requirements.txt`, `Procfile` и `Dockerfile`.
- Для Render / Railway / VPS можно использовать Gunicorn.

## 3) Сохранение результатов
- После завершения проверки результат автоматически сохраняется на сервере в `Project/results/`.
- На странице появляется кнопка **«Сохранить результат»**, которая скачивает JSON.

## Как запускать локально

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # или просто задайте переменные окружения вручную
export POLZA_API_KEY=...
python Project/app.py
```

Открыть:
- `http://127.0.0.1:5000`
- или с других устройств в сети: `http://<IP_компьютера>:5000`

## Render
Build Command:
```bash
pip install -r requirements.txt
```

Start Command:
```bash
gunicorn --chdir Project app:app --workers 1 --threads 8 --timeout 300
```

Не забудьте добавить переменную окружения:
- `POLZA_API_KEY`

## Важно
Сейчас очередь и статусы хранятся в памяти процесса. Это нормально для одного инстанса / одного процесса. Если захотите масштабировать приложение на несколько инстансов, нужно будет вынести очередь и хранение задач в Redis / БД.
