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

from flask import Flask, jsonify, render_template, request, send_file
from openai import OpenAI
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from Prompts import SYSTEM_PROMPT


BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_CONTENT_LENGTH_MB = int(os.getenv("MAX_CONTENT_LENGTH_MB", "30"))
CHECKER_THREADS = max(1, int(os.getenv("CHECKER_THREADS", "4")))
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-5.4")
POLZA_BASE_URL = os.getenv("POLZA_BASE_URL", "https://polza.ai/api/v1")
POLZA_API_KEY = os.getenv("POLZA_API_KEY", "").strip()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH_MB * 1024 * 1024

executor = ThreadPoolExecutor(max_workers=CHECKER_THREADS, thread_name_prefix="olympiad-checker")
jobs_lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}


def get_client() -> OpenAI:
    if not POLZA_API_KEY:
        raise RuntimeError(
            "Не задан POLZA_API_KEY. Добавьте его в переменные окружения."
        )

    return OpenAI(
        base_url=POLZA_BASE_URL,
        api_key=POLZA_API_KEY,
    )


def pdf_bytes_to_data_url(raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("utf-8")
    return f"data:application/pdf;base64,{encoded}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_messages(file1_name: str, file1_bytes: bytes, file2_name: str, file2_bytes: bytes) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Проанализируй эти документы:"},
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


def create_job(file1_name: str, file2_name: str) -> str:
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "message": "Задача поставлена в очередь.",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "file_names": [file1_name, file2_name],
            "result": None,
            "download_path": None,
            "error": None,
        }
    return job_id


def save_result_to_disk(job_id: str, result_payload: Any) -> Path:
    filename = f"result_{job_id}.json"
    path = RESULTS_DIR / filename
    payload = {
        "job_id": job_id,
        "saved_at": utc_now_iso(),
        "result": result_payload,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_check(job_id: str, file1_name: str, file1_bytes: bytes, file2_name: str, file2_bytes: bytes) -> None:
    update_job(
        job_id,
        status="processing",
        message="Идёт проверка файлов. Это может занять некоторое время.",
        updated_at=utc_now_iso(),
    )

    try:
        client = get_client()
        messages = build_messages(file1_name, file1_bytes, file2_name, file2_bytes)
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            extra_body={
                "reasoning": {
                    "enabled": True,
                    "effort": "low",
                }
            },
        )

        raw_result = completion.choices[0].message.content or ""
        result_payload, _result_kind = normalize_result(raw_result)
        saved_path = save_result_to_disk(job_id, result_payload)

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
    return jsonify({"error": f"Файлы слишком большие. Лимит: {MAX_CONTENT_LENGTH_MB} МБ"}), 413


@app.get("/")
def index():
    return render_template("index.html", checker_threads=CHECKER_THREADS)


@app.get("/healthz")
def healthz():
    return jsonify(
        {
            "ok": True,
            "threads": CHECKER_THREADS,
            "results_dir": str(RESULTS_DIR),
            "has_api_key": bool(POLZA_API_KEY),
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

        safe_name_1 = secure_filename(file1.filename) or "file1.pdf"
        safe_name_2 = secure_filename(file2.filename) or "file2.pdf"

        file1_bytes = file1.read()
        file2_bytes = file2.read()

        if not file1_bytes or not file2_bytes:
            return jsonify({"error": "Оба файла должны быть непустыми"}), 400

        job_id = create_job(safe_name_1, safe_name_2)
        executor.submit(run_check, job_id, safe_name_1, file1_bytes, safe_name_2, file2_bytes)

        return jsonify(
            {
                "job_id": job_id,
                "status": "queued",
                "message": "Файлы приняты. Проверка запущена в фоне.",
                "status_url": f"/result/{job_id}",
            }
        ), 202
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
