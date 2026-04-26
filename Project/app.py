import base64
import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
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


def get_client(settings: ClientSettings) -> OpenAI:
    if not settings.polza_api_key:
        raise RuntimeError(
            "Не задан POLZA_API_KEY. Добавьте его в .env или переопределите в конфиге клиента."
        )

    return OpenAI(
        base_url=settings.polza_base_url,
        api_key=settings.polza_api_key,
    )


def pdf_bytes_to_data_url(raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:application/pdf;base64,{encoded}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_messages(
    settings: ClientSettings,
    file1_name: str,
    file1_bytes: bytes,
    file2_name: str,
    file2_bytes: bytes,
) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": settings.system_prompt,
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": settings.request_intro_text},
                {
                    "type": "file",
                    "file": {
                        "filename": file1_name,
                        "file_data": pdf_bytes_to_data_url(file1_bytes),
                    },
                },
                {
                    "type": "file",
                    "file": {
                        "filename": file2_name,
                        "file_data": pdf_bytes_to_data_url(file2_bytes),
                    },
                },
            ],
        },
    ]


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


def validate_upload_size(settings: ClientSettings, file1_bytes: bytes, file2_bytes: bytes) -> str | None:
    limit_bytes = settings.max_content_length_mb * 1024 * 1024
    total_size = len(file1_bytes) + len(file2_bytes)
    if total_size > limit_bytes:
        return (
            f"Суммарный размер файлов превышает лимит клиента {settings.display_name}: "
            f"{settings.max_content_length_mb} МБ."
        )
    return None


def create_job(file1_name: str, file2_name: str, settings: ClientSettings) -> str:
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "message": "Задача поставлена в очередь.",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "file_names": [file1_name, file2_name],
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
    file1_name: str,
    file1_bytes: bytes,
    file2_name: str,
    file2_bytes: bytes,
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
        messages = build_messages(settings, file1_name, file1_bytes, file2_name, file2_bytes)

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
        file1 = request.files.get("file1")
        file2 = request.files.get("file2")

        if not file1 or not file2:
            return jsonify({"error": "Нужно загрузить 2 PDF файла"}), 400

        if not file1.filename or not file2.filename:
            return jsonify({"error": "У файлов должны быть имена"}), 400

        if not file1.filename.lower().endswith(".pdf") or not file2.filename.lower().endswith(".pdf"):
            return jsonify({"error": "Можно загружать только PDF"}), 400

        requested_client_id = extract_requested_client_id()
        try:
            settings = resolve_client_settings(requested_client_id)
        except ValueError as exc:
            return jsonify({"error": f"Ошибка конфигурации клиента: {exc}"}), 400

        safe_name_1 = secure_filename(file1.filename) or "file1.pdf"
        safe_name_2 = secure_filename(file2.filename) or "file2.pdf"

        file1_bytes = file1.read()
        file2_bytes = file2.read()

        if not file1_bytes or not file2_bytes:
            return jsonify({"error": "Оба файла должны быть непустыми"}), 400

        size_error = validate_upload_size(settings, file1_bytes, file2_bytes)
        if size_error:
            return jsonify({"error": size_error, "client": settings.to_public_dict()}), 413

        job_id = create_job(safe_name_1, safe_name_2, settings)
        executor.submit(run_check, job_id, safe_name_1, file1_bytes, safe_name_2, file2_bytes, settings)

        return (
            jsonify(
                {
                    "job_id": job_id,
                    "status": "queued",
                    "message": "Файлы приняты. Проверка запущена в фоне.",
                    "status_url": f"/result/{job_id}",
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
