document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("pdf-form");
  const taskText = document.getElementById("task-text");
  const solutionText = document.getElementById("solution-text");
  const taskFiles = document.getElementById("task-files");
  const solutionFiles = document.getElementById("solution-files");
  const taskFileList = document.getElementById("task-file-list");
  const solutionFileList = document.getElementById("solution-file-list");
  const resultBlock = document.getElementById("result");
  const statusBlock = document.getElementById("status");
  const jobMetaBlock = document.getElementById("job-meta");
  const submitButton = document.getElementById("submit-btn");
  const downloadLink = document.getElementById("download-link");

  let pollTimer = null;

  function formatBytes(bytes) {
    if (!bytes) {
      return "0 Б";
    }

    const units = ["Б", "КБ", "МБ", "ГБ"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, index);
    return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function resetDownloadLink() {
    downloadLink.hidden = true;
    downloadLink.href = "#";
  }

  function setStatus(message, cssClass = "") {
    statusBlock.className = cssClass;
    statusBlock.textContent = message;
  }

  function clearPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  function renderFileList(input, list) {
    list.textContent = "";

    Array.from(input.files).forEach((file) => {
      const chip = document.createElement("div");
      const name = document.createElement("span");
      const size = document.createElement("small");

      chip.className = "file-chip";
      name.textContent = file.name;
      size.textContent = formatBytes(file.size);

      chip.append(name, size);
      list.appendChild(chip);
    });
  }

  function bindFileInput(input, list) {
    input.addEventListener("change", () => renderFileList(input, list));
  }

  function bindDropZone(box) {
    const input = box.querySelector('input[type="file"]');
    const list = input.id === "task-files" ? taskFileList : solutionFileList;

    ["dragenter", "dragover"].forEach((eventName) => {
      box.addEventListener(eventName, (event) => {
        event.preventDefault();
        box.classList.add("is-dragover");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      box.addEventListener(eventName, (event) => {
        event.preventDefault();
        box.classList.remove("is-dragover");
      });
    });

    box.addEventListener("drop", (event) => {
      if (!event.dataTransfer?.files?.length) {
        return;
      }

      input.files = event.dataTransfer.files;
      renderFileList(input, list);
    });
  }

  function hasSideContent(textarea, fileInput) {
    return textarea.value.trim().length > 0 || fileInput.files.length > 0;
  }

  function buildJobMeta(data) {
    const parts = [];

    if (data.job_id) {
      parts.push(`Задача: ${data.job_id}`);
    }

    if (data.client) {
      const effectiveClient = data.client.effective_client_id || "default";
      const displayName = data.client.display_name || effectiveClient;
      parts.push(`Клиент: ${displayName} (${effectiveClient})`);
      parts.push(`Конфиг: ${data.client.config_source || "default"}`);
      parts.push(`Модель: ${data.client.model_name}`);
    }

    if (Array.isArray(data.file_names) && data.file_names.length > 0) {
      parts.push(`Файлов: ${data.file_names.length}`);
    }

    if (data.updated_at) {
      parts.push(`Обновлено: ${data.updated_at}`);
    }

    return parts.join(" · ");
  }

  async function pollResult(jobId) {
    try {
      const response = await fetch(`/result/${jobId}`);
      const data = await response.json();

      if (!response.ok) {
        setStatus(data.error || "Не удалось получить статус задачи.", "error");
        submitButton.disabled = false;
        return;
      }

      jobMetaBlock.textContent = buildJobMeta(data);
      setStatus(data.message || "Обработка...", data.status === "error" ? "error" : "");

      if (data.status === "queued" || data.status === "processing") {
        pollTimer = setTimeout(() => pollResult(jobId), 2000);
        return;
      }

      submitButton.disabled = false;

      if (data.status === "done") {
        setStatus("Проверка завершена.", "success");
        resultBlock.textContent =
          typeof data.result === "string"
            ? data.result
            : JSON.stringify(data.result, null, 2);

        if (data.download_url) {
          downloadLink.href = data.download_url;
          downloadLink.hidden = false;
        }
        return;
      }

      if (data.status === "error") {
        resultBlock.textContent = data.error || "Неизвестная ошибка";
        return;
      }

      resultBlock.textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      submitButton.disabled = false;
      setStatus(`Ошибка запроса: ${error.message}`, "error");
    }
  }

  bindFileInput(taskFiles, taskFileList);
  bindFileInput(solutionFiles, solutionFileList);
  document.querySelectorAll("[data-upload-box]").forEach(bindDropZone);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearPolling();
    resetDownloadLink();

    if (!hasSideContent(taskText, taskFiles)) {
      setStatus("Добавьте текст или файл с условием задания.", "error");
      taskText.focus();
      return;
    }

    if (!hasSideContent(solutionText, solutionFiles)) {
      setStatus("Добавьте текст или файл с решением ученика.", "error");
      solutionText.focus();
      return;
    }

    const formData = new FormData(form);
    resultBlock.textContent = "";
    jobMetaBlock.textContent = "";
    submitButton.disabled = true;
    setStatus("Материалы загружаются...", "");

    try {
      const response = await fetch("/upload", {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        submitButton.disabled = false;
        setStatus(data.error || "Ошибка", "error");
        if (data.client) {
          jobMetaBlock.textContent = buildJobMeta(data);
        }
        return;
      }

      setStatus(data.message || "Задача создана.");
      jobMetaBlock.textContent = buildJobMeta(data);
      resultBlock.textContent = "Ожидаем результат проверки...";
      await pollResult(data.job_id);
    } catch (error) {
      submitButton.disabled = false;
      setStatus(`Ошибка запроса: ${error.message}`, "error");
    }
  });
});
