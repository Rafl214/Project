import base64
import json
import logging
import mimetypes
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from openai import OpenAI
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
load_dotenv(ROOT_DIR / ".env")

from client_config import ClientSettings, get_client_configs_dir, list_registered_clients, resolve_client_settings  # noqa: E402


RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CHECKER_THREADS = max(1, int(os.getenv("CHECKER_THREADS", "4")))
SERVER_MAX_CONTENT_LENGTH_MB = max(
    1,
    int(os.getenv("SERVER_MAX_CONTENT_LENGTH_MB", os.getenv("MAX_CONTENT_LENGTH_MB", "30"))),
)
FRONTEND_ENABLED = os.getenv("ENABLE_FRONTEND", "1") == "1"
CLIENT_ID_HEADER = os.getenv("CLIENT_ID_HEADER", "X-Client-ID")
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "0") == "1"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = SERVER_MAX_CONTENT_LENGTH_MB * 1024 * 1024

if TRUST_PROXY_HEADERS:
    # Allow correct scheme/host detection when the service sits behind a trusted reverse proxy or tunnel.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)  # type: ignore[assignment]

executor = ThreadPoolExecutor(max_workers=CHECKER_THREADS, thread_name_prefix="olympiad-checker")
jobs_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class UploadedInputFile:
    section: str
    filename: str
    mime_type: str
    data: bytes


def get_client(settings: ClientSettings) -> OpenAI:
    if not settings.polza_api_key:
        raise RuntimeError(
            "Не задан POLZA_API_KEY. Добавьте его в .env или переопределите в конфиге клиента."
        )

    return OpenAI(
        base_url=settings.polza_base_url,
        api_key=settings.polza_api_key,
    )


def bytes_to_data_url(raw: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_messages(
    settings: ClientSettings,
    task_text: str,
    task_files: list[UploadedInputFile],
    solution_text: str,
    solution_files: list[UploadedInputFile],
) -> list[dict[str, Any]]:
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": settings.request_intro_text},
    ]

    if task_text:
        user_content.append({"type": "text", "text": f"Условие задания:\n{task_text}"})

    for uploaded_file in task_files:
        user_content.append(
            {
                "type": "text",
                "text": f"Файл с условием: {uploaded_file.filename} ({uploaded_file.mime_type})",
            }
        )
        user_content.append(file_to_message_part(uploaded_file))

    if solution_text:
        user_content.append({"type": "text", "text": f"Решение ученика:\n{solution_text}"})

    for uploaded_file in solution_files:
        user_content.append(
            {
                "type": "text",
                "text": f"Файл с решением ученика: {uploaded_file.filename} ({uploaded_file.mime_type})",
            }
        )
        user_content.append(file_to_message_part(uploaded_file))

    return [
        {
            "role": "system",
            "content": settings.system_prompt,
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]


def file_to_message_part(uploaded_file: UploadedInputFile) -> dict[str, Any]:
    return {
        "type": "file",
        "file": {
            "filename": uploaded_file.filename,
            "file_data": bytes_to_data_url(uploaded_file.data, uploaded_file.mime_type),
        },
    }


def strip_code_fences(text: str) -> str:
    text = text.strip()
    fenced_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced_match:
        return fenced_match.group(1).strip()
    return text


def normalize_result(raw_result: str) -> tuple[Any, str]:
    cleaned = strip_code_fences(raw_result)
    try:
        return json.loads(cleaned), "json"
    except json.JSONDecodeError:
        return {"raw_result": raw_result}, "json"


def update_job(job_id: str, **fields: Any) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def extract_requested_client_id() -> str | None:
    header_client_id = request.headers.get(CLIENT_ID_HEADER)
    if header_client_id and header_client_id.strip():
        return header_client_id.strip()

    form_client_id = request.form.get("client_id")
    if form_client_id and form_client_id.strip():
        return form_client_id.strip()

    query_client_id = request.args.get("client_id")
    if query_client_id and query_client_id.strip():
        return query_client_id.strip()

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        json_client_id = payload.get("client_id")
        if isinstance(json_client_id, str) and json_client_id.strip():
            return json_client_id.strip()

    return None


def guess_mime_type(filename: str, content_type: str | None) -> str:
    guessed_type, _encoding = mimetypes.guess_type(filename)
    if content_type and content_type != "application/octet-stream":
        return content_type
    return guessed_type or content_type or "application/octet-stream"


def read_uploaded_files(field_name: str, section: str) -> list[UploadedInputFile]:
    uploaded_files: list[UploadedInputFile] = []

    for index, storage in enumerate(request.files.getlist(field_name), start=1):
        if not storage or not storage.filename:
            continue

        raw = storage.read()
        if not raw:
            continue

        filename = secure_filename(storage.filename) or Path(storage.filename).name or f"{section}_{index}"
        uploaded_files.append(
            UploadedInputFile(
                section=section,
                filename=filename,
                mime_type=guess_mime_type(filename, storage.content_type),
                data=raw,
            )
        )

    return uploaded_files


def collect_upload_payload() -> tuple[str, list[UploadedInputFile], str, list[UploadedInputFile]]:
    task_text = (request.form.get("task_text") or request.form.get("condition_text") or "").strip()
    solution_text = (request.form.get("student_solution") or request.form.get("solution_text") or "").strip()

    task_files = read_uploaded_files("task_files", "task") + read_uploaded_files("condition_files", "task")
    solution_files = read_uploaded_files("solution_files", "solution") + read_uploaded_files(
        "student_solution_files",
        "solution",
    )

    # Backward compatibility with the previous two-PDF API contract.
    task_files.extend(read_uploaded_files("file1", "task"))
    solution_files.extend(read_uploaded_files("file2", "solution"))

    return task_text, task_files, solution_text, solution_files


def validate_upload_presence(
    task_text: str,
    task_files: list[UploadedInputFile],
    solution_text: str,
    solution_files: list[UploadedInputFile],
) -> str | None:
    if not task_text and not task_files:
        return "Добавьте текст или файл с условием задания."

    if not solution_text and not solution_files:
        return "Добавьте текст или файл с решением ученика."

    return None


def validate_upload_size(settings: ClientSettings, uploaded_files: list[UploadedInputFile]) -> str | None:
    limit_bytes = settings.max_content_length_mb * 1024 * 1024
    total_size = sum(len(uploaded_file.data) for uploaded_file in uploaded_files)
    if total_size > limit_bytes:
        return (
            f"Суммарный размер файлов превышает лимит клиента {settings.display_name}: "
            f"{settings.max_content_length_mb} МБ."
        )
    return None


def create_job(file_names: list[str], settings: ClientSettings) -> str:
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "message": "Задача поставлена в очередь.",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "file_names": file_names,
            "client": settings.to_public_dict(),
            "result": None,
            "download_path": None,
            "error": None,
        }
    return job_id


def save_result_to_disk(job_id: str, result_payload: Any, settings: ClientSettings) -> Path:
    filename = f"result_{job_id}.json"
    path = RESULTS_DIR / filename
    payload = {
        "job_id": job_id,
        "saved_at": utc_now_iso(),
        "client": settings.to_public_dict(),
        "result": result_payload,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_check(
    job_id: str,
    task_text: str,
    task_files: list[UploadedInputFile],
    solution_text: str,
    solution_files: list[UploadedInputFile],
    settings: ClientSettings,
) -> None:
    update_job(
        job_id,
        status="processing",
        message="Идёт проверка файлов. Это может занять некоторое время.",
        updated_at=utc_now_iso(),
    )

    try:
        client = get_client(settings)
        messages = build_messages(settings, task_text, task_files, solution_text, solution_files)

        completion_kwargs: dict[str, Any] = {
            "model": settings.model_name,
            "messages": messages,
        }
        if settings.reasoning_enabled:
            completion_kwargs["extra_body"] = {
                "reasoning": {
                    "enabled": True,
                    "effort": settings.reasoning_effort,
                }
            }

        completion = client.chat.completions.create(**completion_kwargs)

        raw_result = completion.choices[0].message.content or ""
        result_payload, _result_kind = normalize_result(raw_result)
        saved_path = save_result_to_disk(job_id, result_payload, settings)

        update_job(
            job_id,
            status="done",
            message="Проверка завершена.",
            result=result_payload,
            download_path=str(saved_path),
            updated_at=utc_now_iso(),
        )
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Job %s failed", job_id)
        update_job(
            job_id,
            status="error",
            message="Во время обработки произошла ошибка.",
            error=str(exc),
            updated_at=utc_now_iso(),
        )


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(_error: RequestEntityTooLarge):
    return (
        jsonify(
            {
                "error": (
                    "Файлы слишком большие для сервера. "
                    f"Текущий серверный предел: {SERVER_MAX_CONTENT_LENGTH_MB} МБ."
                )
            }
        ),
        413,
    )


@app.get("/")
def index():
    if not FRONTEND_ENABLED:
        return jsonify(
            {
                "ok": True,
                "frontend_enabled": False,
                "message": "Frontend отключён. Используйте API-эндпоинт POST /upload.",
                "client_id_header": CLIENT_ID_HEADER,
                "public_base_url": PUBLIC_BASE_URL or None,
                "registered_clients": list_registered_clients(),
            }
        )

    return render_template(
        "index.html",
        checker_threads=CHECKER_THREADS,
        client_id_header=CLIENT_ID_HEADER,
    )


@app.get("/healthz")
def healthz():
    try:
        default_settings = resolve_client_settings(None)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify(
        {
            "ok": True,
            "threads": CHECKER_THREADS,
            "results_dir": str(RESULTS_DIR),
            "frontend_enabled": FRONTEND_ENABLED,
            "client_id_header": CLIENT_ID_HEADER,
            "client_configs_dir": str(get_client_configs_dir()),
            "web_host": os.getenv("WEB_HOST", "0.0.0.0"),
            "port": int(os.getenv("PORT", os.getenv("WEB_PORT", "5000"))),
            "trust_proxy_headers": TRUST_PROXY_HEADERS,
            "public_base_url": PUBLIC_BASE_URL or None,
            "registered_clients": list_registered_clients(),
            "default_client": default_settings.to_public_dict(),
        }
    )


@app.post("/upload")
def upload():
    try:
        requested_client_id = extract_requested_client_id()
        try:
            settings = resolve_client_settings(requested_client_id)
        except ValueError as exc:
            return jsonify({"error": f"Ошибка конфигурации клиента: {exc}"}), 400

        task_text, task_files, solution_text, solution_files = collect_upload_payload()
        presence_error = validate_upload_presence(task_text, task_files, solution_text, solution_files)
        if presence_error:
            return jsonify({"error": presence_error}), 400

        uploaded_files = task_files + solution_files
        size_error = validate_upload_size(settings, uploaded_files)
        if size_error:
            return jsonify({"error": size_error, "client": settings.to_public_dict()}), 413

        file_names = [uploaded_file.filename for uploaded_file in uploaded_files]
        job_id = create_job(file_names, settings)
        executor.submit(run_check, job_id, task_text, task_files, solution_text, solution_files, settings)

        return (
            jsonify(
                {
                    "job_id": job_id,
                    "status": "queued",
                    "message": "Материалы приняты. Проверка запущена в фоне.",
                    "status_url": f"/result/{job_id}",
                    "file_names": file_names,
                    "client": settings.to_public_dict(),
                }
            ),
            202,
        )
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Upload failed")
        return jsonify({"error": f"Ошибка обработки: {exc}"}), 500


@app.get("/result/<job_id>")
def get_result(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Результат не найден"}), 404

    response: dict[str, Any] = {
        "job_id": job["job_id"],
        "status": job["status"],
        "message": job["message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "file_names": job.get("file_names", []),
        "client": job["client"],
    }

    if job["status"] == "done":
        response["result"] = job["result"]
        response["download_url"] = f"/download/{job_id}"

    if job["status"] == "error":
        response["error"] = job["error"]

    return jsonify(response)


@app.get("/download/<job_id>")
def download_result(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Результат не найден"}), 404

    if job["status"] != "done" or not job["download_path"]:
        return jsonify({"error": "Результат ещё не готов"}), 409

    return send_file(
        job["download_path"],
        as_attachment=True,
        download_name=f"olympiad_result_{job_id}.json",
        mimetype="application/json",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("WEB_PORT", "5000")))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)
