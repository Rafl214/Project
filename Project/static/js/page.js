document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("pdf-form");
    const resultBlock = document.getElementById("result");
    const statusBlock = document.getElementById("status");

    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const formData = new FormData(form);
        resultBlock.textContent = "";
        statusBlock.textContent = "Идёт обработка...";

        try {
            const response = await fetch("/upload", {
                method: "POST",
                body: formData
            });

            const data = await response.json();

            if (!response.ok) {
                statusBlock.innerHTML = `<p class="error">${data.error || "Ошибка"}</p>`;
                return;
            }

            statusBlock.textContent = "Готово";
            resultBlock.textContent =
                typeof data.result === "string"
                    ? data.result
                    : JSON.stringify(data.result, null, 2);

        } catch (error) {
            statusBlock.innerHTML = `<p class="error">Ошибка запроса: ${error.message}</p>`;
        }
    });
});