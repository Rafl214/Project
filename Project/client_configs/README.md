# Клиентские конфиги

В этой папке можно хранить JSON-файлы с переопределениями для конкретных клиентов.

## Как это работает

- Базовые настройки берутся из `.env` и `Project/Prompts.py`.
- Если в запросе передан `client_id`, сервис ищет файл `Project/client_configs/<client_id>.json`.
- Если файл найден, его поля переопределяют базовые настройки.
- Если файл не найден, сервис продолжает работать на базовых настройках.

Имя файла должно совпадать с `client_id` после нормализации:

- буквы приводятся к нижнему регистру;
- пробелы заменяются на `-`;
- разрешены только символы `a-z`, `0-9`, `.`, `_`, `-`.

Например:

- `School 7` -> `school-7.json`
- `client_alpha` -> `client_alpha.json`

## Поддерживаемые поля

```json
{
  "display_name": "Школа 7",
  "model_name": "openai/gpt-5.4",
  "polza_base_url": "https://polza.ai/api/v1",
  "polza_api_key": "client_specific_key",
  "request_intro_text": "Проверь эти документы по правилам клиента Школа 7:",
  "system_prompt": "Полный текст системного промпта",
  "system_prompt_file": "prompts/school-7_system_prompt.txt",
  "reasoning_enabled": true,
  "reasoning_effort": "medium",
  "max_content_length_mb": 30
}
```

Обычно достаточно переопределять только нужные поля, остальные можно не указывать.

## Пример

Готовые примеры лежат в `Project/client_configs/examples/`.
Также в репозитории уже есть рабочий тестовый конфиг `Project/client_configs/demo-school.json`.

Чаще всего процесс такой:

1. Скопировать `examples/demo-school.json` в `Project/client_configs/demo-school.json`.
2. При необходимости скопировать и отредактировать prompt-файл.
3. Отправлять запросы с `client_id=demo-school` или заголовком `X-Client-ID: demo-school`.
