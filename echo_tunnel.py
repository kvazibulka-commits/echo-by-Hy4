#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ЭХО (Echo Radar) — Автоматический публичный туннель (echo_tunnel.py)
Запускает защищенный HTTPS-адрес через Cloudflare Tunnel (по протоколу HTTP/2 для стабильной работы в РФ)
без регистрации и без белого IP, чтобы сервер 'echo_server.py' был доступен через 4G/LTE.
"""

import json
import os
import re
import socket
import subprocess
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLOUDFLARED_EXE = os.path.join(BASE_DIR, "cloudflared.exe")
CONFIG_PATH = os.path.join(BASE_DIR, "echo_config.json")
LOCAL_PORT = int(os.environ.get("PORT", 8080))
DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"


def ensure_cloudflared() -> bool:
    if os.path.exists(CLOUDFLARED_EXE) and os.path.getsize(CLOUDFLARED_EXE) > 5_000_000:
        return True

    print("=" * 66)
    print(" [ЭХО] cloudflared.exe не найден. Начинаем автоматическую загрузку...")
    print(f" Источник: {DOWNLOAD_URL}")
    print("=" * 66)

    tmp_path = CLOUDFLARED_EXE + ".tmp"
    try:
        req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "EchoRadar/3.0"})
        with urllib.request.urlopen(req, timeout=45) as resp, open(tmp_path, "wb") as out_file:
            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            chunk_size = 1024 * 512
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    percent = int((downloaded / total_size) * 100)
                    print(
                        f" Загрузка: {percent}% ({downloaded // (1024 * 1024)} МБ / {total_size // (1024 * 1024)} МБ)",
                        end="\r",
                        flush=True
                    )
        if os.path.exists(CLOUDFLARED_EXE):
            os.remove(CLOUDFLARED_EXE)
        os.replace(tmp_path, CLOUDFLARED_EXE)
        print("\n [ЭХО] cloudflared.exe успешно загружен и готов к запуску!\n")
        return True
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        print(f"\n [Ошибка загрузки] Не удалось автоматически скачать cloudflared: {e}")
        print(" Скачайте вручную файл cloudflared-windows-amd64.exe с GitHub,")
        print(f" переименуйте в 'cloudflared.exe' и положите в папку: {BASE_DIR}")
        return False


def check_local_server(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def save_tunnel_to_config(tunnel_url: str, clean_host: str):
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    cfg["public_tunnel_url"] = tunnel_url
    cfg["public_tunnel_host"] = clean_host

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def copy_to_windows_clipboard(text: str) -> bool:
    if os.name != "nt":
        return False
    try:
        proc = subprocess.Popen(["clip"], stdin=subprocess.PIPE, close_fds=True)
        proc.communicate(input=text.encode("utf-8"))
        return proc.returncode == 0
    except Exception:
        return False


def main():
    if not ensure_cloudflared():
        sys.exit(1)

    if not check_local_server(LOCAL_PORT):
        print("!" * 66)
        print(f" [ВНИМАНИЕ] Сервер ЭХО еще не запущен на 127.0.0.1:{LOCAL_PORT}!")
        print(" Обязательно запустите в соседнем окне: python echo_server.py")
        print("!" * 66 + "\n")

    print("=" * 66)
    print(f" [ЭХО] Запуск защищенного HTTP/2 туннеля для 127.0.0.1:{LOCAL_PORT}...")
    print(" Ожидание генерации публичного HTTPS-адреса Cloudflare...")
    print("=" * 66)

    # --protocol http2 и --edge-ip-version 4 гарантируют обход блокировок UDP/QUIC
    cmd = [
        CLOUDFLARED_EXE,
        "tunnel",
        "--url", f"http://127.0.0.1:{LOCAL_PORT}",
        "--protocol", "http2",
        "--edge-ip-version", "4",
        "--no-autoupdate"
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        encoding="utf-8",
        errors="ignore"
    )

    tunnel_url = None
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    try:
        if proc.stdout is not None:
            for line in iter(proc.stdout.readline, ""):
                match = url_pattern.search(line)
                if match and not tunnel_url:
                    tunnel_url = match.group(0)
                    clean_host = tunnel_url.replace("https://", "").replace("http://", "").strip()

                    save_tunnel_to_config(tunnel_url, clean_host)
                    copied = copy_to_windows_clipboard(clean_host)

                    print("\n" + "=" * 66)
                    print(" 🌐 ВАШ СЕРВЕР ЭХО ТЕПЕРЬ ДОСТУПЕН В ИНТЕРНЕТЕ!")
                    print(f" Публичный адрес сонара (iPad/Web) : {tunnel_url}/app")
                    print(f" Адрес для Android-приложения (⚙)  : {clean_host}")
                    if copied:
                        print(" 📋 Адрес автоматически скопирован в буфер обмена Windows!")
                    print("=" * 66)
                    print(f" Вставьте в настройках приложения (⚙) адрес: {clean_host}")
                    print(" Теперь сонар работает на улице через мобильный 4G/LTE без Wi-Fi.")
                    print(" Не закрывайте это окно, пока пользуетесь приложением вне дома.\n")
                elif "ERR" in line and "trycloudflare" not in line:
                    print(f" [Статус туннеля]: {line.strip()}")
    except KeyboardInterrupt:
        print("\n [ЭХО] Остановка публичного туннеля...")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    main()