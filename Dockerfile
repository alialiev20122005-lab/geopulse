FROM python:3.12-slim

# Системные зависимости и curl для healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создание непривилегированного пользователя appuser
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Копируем и устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код проекта
COPY . .

# Создаем папку для постоянного хранилища SQLite и назначаем владельца
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

# Порт приложения
EXPOSE 8000

# Проверка работоспособности сервиса
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

# Запуск Uvicorn сервера
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
