document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("pdf-form");
  const resultBlock = document.getElementById("result");
  const statusBlock = document.getElementById("status");
  const jobMetaBlock = document.getElementById("job-meta");
  const submitButton = document.getElementById("submit-btn");
  const downloadLink = document.getElementById("download-link");

  let pollTimer = null;

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

  async function pollResult(jobId) {
    try {
      const response = await fetch(`/result/${jobId}`);
      const data = await response.json();

      if (!response.ok) {
        setStatus(data.error || "Не удалось получить статус задачи.", "error");
        submitButton.disabled = false;
        return;
      }

      jobMetaBlock.textContent = `Задача: ${data.job_id} • Обновлено: ${data.updated_at}`;
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

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearPolling();
    resetDownloadLink();

    const formData = new FormData(form);
    resultBlock.textContent = "";
    jobMetaBlock.textContent = "";
    submitButton.disabled = true;
    setStatus("Файлы загружаются...", "");

    try {
      const response = await fetch("/upload", {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (!response.ok) {
        submitButton.disabled = false;
        setStatus(data.error || "Ошибка", "error");
        return;
      }

      setStatus(data.message || "Задача создана.");
      jobMetaBlock.textContent = `Задача: ${data.job_id}`;
      resultBlock.textContent = "Ожидаем результат проверки...";
      await pollResult(data.job_id);
    } catch (error) {
      submitButton.disabled = false;
      setStatus(`Ошибка запроса: ${error.message}`, "error");
    }
  });
});
