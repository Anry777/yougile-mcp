#!/usr/bin/env sh
set -eu

# Проверка, что скрипт запущен от root
if [ "$(id -u)" -ne 0 ]; then
  echo "Этот скрипт нужно запускать от root (через sudo или под root-пользователем)." >&2
  exit 1
fi

echo "Обновление списка пакетов..."
apt-get update -y

echo "Установка базовых зависимостей..."
apt-get install -y \
  software-properties-common \
  ca-certificates \
  python3 \
  python3-pip \
  python3-venv \
  sshpass

echo "Установка Ansible..."
apt-get install -y ansible

echo "Проверка установки..."
ansible --version || {
  echo "Ansible не установлен или не найден в PATH." >&2
  exit 1
}

echo "Ansible успешно установлен."
