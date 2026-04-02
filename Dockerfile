FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PORT=5000

CMD ["gunicorn", "--chdir", "Project", "app:app", "--workers", "1", "--threads", "8", "--timeout", "300", "--bind", "0.0.0.0:5000"]
