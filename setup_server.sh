#!/bin/bash
# Скрипт автоматической настройки чистого сервера Ubuntu 22.04 / 24.04 для GeoPulse

set -e

echo "=========================================="
echo "🚀 Начало настройки сервера для GeoPulse..."
echo "=========================================="

# 1. Обновление пакетов
apt-get update && apt-get upgrade -y

# 2. Установка Docker и Docker Compose
if ! command -v docker &> /dev/null; then
    echo "📦 Установка Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

# 3. Установка Git и утилит
apt-get install -y git curl ufw

# 4. Настройка фаервола (UFW)
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "=========================================="
echo "✅ Сервер готов к запуску контейнеров!"
echo "Для запуска проекта выполните:"
echo "  docker compose up -d --build"
echo "=========================================="
