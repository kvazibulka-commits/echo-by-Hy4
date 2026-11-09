#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ЭХО (Echo Radar) — ПК-узел (echo_pc.py)
Философия: «Ты видишь не людей вокруг. Ты видишь музыку, которая сейчас живет вокруг тебя».

ПК-узел показывает играющую на Windows музыку серверу ЭХО и печатает входящие импульсы.
Только стандартная библиотека + опциональные winrt-пакеты (авторежим через SMTC).

Режимы:
- Авторежим (по умолчанию, если winrt доступен): читает играющий трек через
  Windows SMTC (System Media Transport Controls) и отправляет его на сервер.
- Ручной режим (--manual или авто-фолбэк): вы вводите «Исполнитель — Название» вручную.

Установка winrt для авторежима (Python 3.13):
    pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation

Примеры:
    python echo_pc.py                                  # авторежим, сервер http://127.0.0.1:8080
    python echo_pc.py --server http://192.168.1.5:8080 # сервер на другой машине
    python echo_pc.py --zone "Покровка · Ночные кофейни"
    python echo_pc.py --manual                         # ручной ввод треков
    python echo_pc.py --once                           # показать текущий трек и выйти
"""

import argparse
import asyncio
import json
import os
import platform
import re
import sys
import urllib.parse
import urllib.request

# ── Опциональные winrt-пакеты: авторежим через SMTC ──
try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )
    WINRT_AVAILABLE = True
except Exception:
    WINRT_AVAILABLE = False

HTTP_TIMEOUT = 3.0
# Токен публичного туннеля: только заголовок, никогда не попадает в логи и HTML.
API_TOKEN = os.environ.get("ECHO_API_TOKEN", "").strip()
WINRT_INSTALL_HINT = (
    "   Для авторежима: pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation"
)


# ── Чтобы ✦, ♫ и кириллица не ломали вывод в любой консоли ──
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass


def default_device_id() -> str:
    """Идентификатор ПК-узла: pc_studio_node (совпадает с дашбордом, чтобы
    входящие импульсы были видны и в консоли, и в браузере)."""
    return "pc_studio_node"


def normalize_server(server: str) -> str:
    """Дополняет адрес схемой http://, если её нет, и убирает хвостовой слэш."""
    server = server.strip().rstrip("/")
    if "://" not in server:
        server = "http://" + server
    return server


def extract_app_name(aumid: str) -> str:
    """Имя приложения из AUMID: часть после последней '.' или '!'."""
    aumid = (aumid or "").strip()
    if not aumid:
        return "приложение"
    name = re.split(r"[.!]", aumid)[-1].strip()
    return name or "приложение"


# ── HTTP-слой (stdlib) ──

def http_post_json(url: str, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "EchoPC/1.0"}
    if API_TOKEN:
        headers["X-API-Key"] = API_TOKEN
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def http_get_json(url: str):
    headers = {"User-Agent": "EchoPC/1.0"}
    if API_TOKEN:
        headers["X-API-Key"] = API_TOKEN
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def send_signal(server: str, device_id: str, artist: str, title: str, source_name: str, zone: str) -> bool:
    """POST /api/signal — заявить о своей музыке. Ошибки сети не роняют узел."""
    payload = {
        "id": device_id,
        "artist": artist,
        "title": title,
        "sourceName": source_name,
        "zoneName": zone,
    }
    try:
        http_post_json(f"{server}/api/signal", payload)
        print("   ✦ сигнал ушёл в эфир")
        return True
    except Exception:
        print("   ⚠ сервер не найден — проверьте echo_server.py")
        return False


def fetch_state(server: str, device_id: str):
    """GET /api/state?deviceId=... — состояние эфира (входящие импульсы)."""
    url = f"{server}/api/state?" + urllib.parse.urlencode({"deviceId": device_id})
    try:
        return http_get_json(url)
    except Exception:
        return None


def split_track_line(line: str):
    """«Исполнитель — Название» → (artist, title) или None, если формат не распознан."""
    for sep in ("—", "–", " - "):
        if sep in line:
            artist, title = line.split(sep, 1)
            artist, title = artist.strip(), title.strip()
            if artist and title:
                return artist, title
    return None


def print_farewell():
    print()
    print(" ✦ ПК-узел ЭХО отключен. До новой встречи в эфире!")


# ── Авторежим: чтение SMTC ──

def looks_like_junk(artist: str, title: str) -> bool:
    """Служебные сессии браузеров/расширений не должны попадать в эфир."""
    blob = f"{artist} {title}".lower()
    return "://" in blob or "offscreen.html" in blob or "chrome-extension" in blob


async def read_current_track():
    """Текущий трек через Windows SMTC.

    Перебирает все медиа-сессии, отбрасывает мусор (служебные страницы
    браузеров) и предпочитает сессии со статусом «играет».
    Возвращает (artist, title, album, app_name) или None, если ничего не играет.
    """
    manager = await GlobalSystemMediaTransportControlsSessionManager.request_async()
    fallback = None
    for session in manager.get_sessions():
        try:
            props = await session.try_get_media_properties_async()
        except Exception:
            continue
        artist = (props.artist or "").strip()
        title = (props.title or "").strip()
        album = (props.album_title or "").strip()
        if not artist and not title:
            continue
        if looks_like_junk(artist, title):
            continue
        app_name = extract_app_name(session.source_app_user_model_id)
        entry = (artist, title, album, app_name)
        try:
            status = session.get_playback_info().playback_status
        except Exception:
            status = None
        if status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING:
            return entry
        if fallback is None:
            fallback = entry
    return fallback


def print_track(track):
    artist, title, album, app_name = track
    line = f" ♫ {artist or 'Неизвестный артист'} — {title or 'Без названия'}"
    if album:
        line += f" · {album}"
    line += f" · {app_name}"
    print(line)


async def once_mode():
    """--once: показать текущий трек и выйти (без отправки на сервер)."""
    print(" ♫ Слушаю, что играет на этом компьютере (SMTC)...")
    try:
        track = await read_current_track()
    except Exception as exc:
        print(f" ⚠ Не удалось прочитать SMTC: {exc}")
        return
    if track is None:
        print(" 🌙 Ничего не играет")
        return
    print_track(track)


async def auto_mode(args, server: str, device_id: str):
    """Основной цикл авторежима: SMTC → POST /api/signal, GET /api/state → импульсы."""
    interval = max(0.5, float(args.interval))
    last_track_key = None
    last_banner_id = None
    was_nothing = False
    server_lost = False

    print("=" * 66)
    print(" ♫ ПК-узел ЭХО — транслирую музыку Windows в эфир")
    print(f" 📡 Сервер    : {server}")
    print(f" 🆔 Устройство: {device_id}")
    print(f" 🌆 Зона      : {args.zone}")
    print(f" ⏱ Интервал  : {interval:g} c · остановка: Ctrl+C")
    print("=" * 66)

    while True:
        # 1) Что играет прямо сейчас
        track = None
        try:
            track = await read_current_track()
        except Exception as exc:
            print(f" ⚠ Не удалось прочитать SMTC: {exc}")

        if track is None:
            if not was_nothing:
                was_nothing = True
                print(" 🌙 Ничего не играет")
        else:
            was_nothing = False
            artist, title, album, app_name = track
            track_key = (artist.lower(), title.lower())
            if track_key != last_track_key:
                print_track(track)
                ok = send_signal(server, device_id, artist, title, f"Windows · {app_name}", args.zone)
                last_track_key = track_key if ok else None

        # 2) Опрос сервера: входящие импульсы
        state = fetch_state(server, device_id)
        if state is None:
            if not server_lost:
                server_lost = True
                print(" ⚠ сервер не найден — проверьте echo_server.py")
            # При возврате связи трек уйдёт заново, даже если он не менялся.
            last_track_key = None
        else:
            if server_lost:
                server_lost = False
                print(" ✦ Сервер ЭХО снова на связи")
            banner = state.get("incomingBanner")
            if banner and banner.get("id") != last_banner_id:
                last_banner_id = banner.get("id")
                symbol = str(banner.get("symbol") or "✦")
                print(f" 🌙 Импульс {symbol} от эфира")

        await asyncio.sleep(interval)


# ── Ручной режим ──

def manual_mode(args, server: str, device_id: str):
    print("=" * 66)
    print(" ⌨ ПК-узел ЭХО — ручной режим")
    print(f" 📡 Сервер: {server} · устройство: {device_id}")
    print(" Вводите треки построчно в формате:")
    print("     Исполнитель — Название")
    print(" Пустая строка — пропустить · q — выход")
    print("=" * 66)

    while True:
        try:
            line = input(" ♫ трек> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.lower() == "q":
            break
        parsed = split_track_line(line)
        if parsed is None:
            print("   ⚠ не поняла строку — формат: Исполнитель — Название")
            continue
        artist, title = parsed
        print(f" ♫ {artist} — {title}")
        send_signal(server, device_id, artist, title, "Windows · вручную", args.zone)

    print_farewell()


# ── Точка входа ──

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echo_pc.py",
        description="ЭХО — ПК-узел: транслирует играющую на Windows музыку на сервер ЭХО "
                    "и печатает входящие импульсы.",
        epilog="Адрес сервера по умолчанию берётся из переменной окружения ECHO_SERVER "
               "или http://127.0.0.1:8080.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--server",
                        default=os.environ.get("ECHO_SERVER", "http://127.0.0.1:8080"),
                        help="адрес сервера ЭХО (или переменная ECHO_SERVER)")
    parser.add_argument("--zone",
                        default="Вечерняя улица",
                        help="зона сонара, в которой будет виден ваш сигнал")
    parser.add_argument("--interval",
                        type=float,
                        default=5,
                        help="период опроса сервера, секунд (по умолчанию 5)")
    parser.add_argument("--device-id",
                        default=None,
                        help="идентификатор устройства (по умолчанию pc_<имя компьютера>)")
    parser.add_argument("--token",
                        default="",
                        help="токен ECHO_API_TOKEN для защищённого туннеля")
    parser.add_argument("--once",
                        action="store_true",
                        help="показать текущий трек и выйти")
    parser.add_argument("--manual",
                        action="store_true",
                        help="ручной режим: вводить «Исполнитель — Название» вручную")
    return parser


def main():
    global API_TOKEN
    args = build_arg_parser().parse_args()
    if args.token.strip():
        API_TOKEN = args.token.strip()
    server = normalize_server(args.server)
    device_id = args.device_id or default_device_id()

    if args.once:
        if not WINRT_AVAILABLE:
            print(" ⚠ Авторежим недоступен: не установлены пакеты winrt.")
            print(WINRT_INSTALL_HINT)
            sys.exit(2)
        try:
            asyncio.run(once_mode())
        except KeyboardInterrupt:
            pass
        return

    if args.manual or not WINRT_AVAILABLE:
        if not args.manual and not WINRT_AVAILABLE:
            print(" ⚠ Пакеты winrt не установлены — авторежим недоступен, включаю ручной режим.")
            print(WINRT_INSTALL_HINT)
            print()
        manual_mode(args, server, device_id)
        return

    try:
        asyncio.run(auto_mode(args, server, device_id))
    except KeyboardInterrupt:
        print_farewell()


if __name__ == "__main__":
    main()
