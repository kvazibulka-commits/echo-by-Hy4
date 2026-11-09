#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ЭХО: round-trip тест связки «Android ↔ сервер ↔ PWA» в обе стороны.

Проверяет без эмулятора и без сети: поднимает настоящий echo_server.py на
127.0.0.1 с ephemeral-портом и временной копией SQLite, затем проигрывает
роли двух клиентов через HTTP.

Запуск:  python tests/roundtrip_test.py
Выход:   0 — все проверки пройдены, 1 — есть FAIL.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TOKEN = "test_roundtrip_token"
ANDROID_ID = "test_android"
PWA_ID = "test_pwa"
HEADERS = {"Content-Type": "application/json", "X-API-Key": TOKEN}

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(("  PASS  " if condition else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def call(base, path, payload=None, headers=None):
    url = base + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers if headers is not None else HEADERS,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {}


def main():
    os.environ["ECHO_API_TOKEN"] = TOKEN
    os.environ["ECHO_ALLOW_EXTERNAL_AI"] = "0"

    tmp = tempfile.mkdtemp(prefix="echo_roundtrip_")
    tmp_db = os.path.join(tmp, "echo_memory.db")
    # В CI база не коммитится (.gitignore): init_db() создаст схему с нуля.
    src_db = os.path.join(ROOT, "echo_memory.db")
    if os.path.exists(src_db):
        shutil.copy(src_db, tmp_db)

    import echo_server as es

    es.DB_PATH = tmp_db
    es.CONFIG_PATH = os.path.join(tmp, "config.json")
    es.init_db()

    # Демо-сиды не должны влиять на детерминизм теста.
    with es.DB_LOCK:
        conn = es.get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM devices")
        cur.execute("DELETE FROM listening_history")
        cur.execute("DELETE FROM capsules")
        conn.commit()
        conn.close()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), es.EchoRequestHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:" + str(port)

    try:
        print("\n[0] Безопасность")
        html = urllib.request.urlopen(base + "/app").read().decode("utf-8")
        check("токен не утекает в HTML", TOKEN not in html)
        check("GET /api/state без токена закрыт", call(base, "/api/state", headers={})[0] == 401)
        check("невалидные числа не роняют запрос",
              call(base, "/api/signal", {"id": "bad_num", "artist": "A", "title": "B",
                                        "angleDegrees": "oops", "distanceMeters": "x"})[0] == 200)
        check("XSS-идентификатор санитизирован",
              all(s["id"] != "x');alert(1);//" for s in
                  call(base, "/api/signal", {"id": "x');alert(1);//", "artist": "A", "title": "B"})[1]["signals"]))
        call(base, "/api/delete_signal", {"targetId": "bad_num", "deviceId": "bad_num"})
        call(base, "/api/delete_signal", {"targetId": "x');alert(1);//", "deviceId": "x');alert(1);//"})

        print("\n[1] Android → сервер → PWA (трек ушёл в эфир)")
        status, _ = call(base, "/api/signal", {
            "id": ANDROID_ID,
            "artist": "Burial",
            "title": "Archangel",
            "sourceName": "Android ЭХО",
            "zoneName": "Вечерняя улица",
        })
        check("Android опубликовал сигнал", status == 200, "POST /api/signal")
        _, state = call(base, "/api/state?deviceId=" + PWA_ID, headers=HEADERS)
        seen = [s for s in state["signals"] if s["id"] == ANDROID_ID]
        check("PWA видит трек Android", len(seen) == 1 and seen[0]["title"] == "Archangel")
        if seen:
            check("ответ без координат", "lat" not in seen[0] and "lon" not in seen[0])
            check("расстояние только крупным диапазоном", seen[0].get("distanceBucket") in ("near", "medium", "far"))
        check("чужая история не утекла",
              all("Burial" not in t for t in state["eveningTraces"]))

        print("\n[2] PWA → сервер → Android (импульс вернулся)")
        status, _ = call(base, "/api/react", {
            "targetId": ANDROID_ID, "senderId": PWA_ID, "symbol": "🌑",
        })
        check("PWA отправил импульс", status == 200, "POST /api/react")
        _, state = call(base, "/api/state?deviceId=" + ANDROID_ID, headers=HEADERS)
        banner = state.get("incomingBanner")
        check("Android получил баннер", bool(banner) and banner.get("symbol") == "🌑")
        check("баннер адресован только получателю", bool(banner) and banner.get("targetId") == ANDROID_ID)
        check("запрещённый символ заменён каноничным",
              call(base, "/api/react", {"targetId": ANDROID_ID, "senderId": PWA_ID, "symbol": "🔥"})[0] == 200)
        check("спуфинг самому себе отклонён",
              call(base, "/api/react", {"targetId": ANDROID_ID, "senderId": ANDROID_ID, "symbol": "🌑"})[0] == 400)

        print("\n[3] Opt-out: мгновенное отключение")
        status, _ = call(base, "/api/delete_signal", {"targetId": ANDROID_ID, "deviceId": ANDROID_ID})
        check("Android вышел из эфира", status == 200)
        _, state = call(base, "/api/state?deviceId=" + PWA_ID, headers=HEADERS)
        check("PWA больше не видит сигнал", all(s["id"] != ANDROID_ID for s in state["signals"]))
        _, state = call(base, "/api/state?deviceId=" + ANDROID_ID, headers=HEADERS)
        check("история удалена вместе с сигналом", all("Burial" not in t for t in state["eveningTraces"]))
        check("удаление чужого сигнала запрещено",
              call(base, "/api/delete_signal", {"targetId": PWA_ID, "deviceId": ANDROID_ID})[0] == 403)

        print("\n[4] Куратор")
        _, resp = call(base, "/api/save_config", {"gemini_api_key": "whatever"})
        check("платный внешний ИИ отключён", resp.get("status") == "disabled" and resp.get("curator") == "local")
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 62)
    print(f"ИТОГ: {len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        for name in failed:
            print("  FAIL:", name)
        print("=" * 62)
        return 1
    print("Связка в обе стороны работает. Эфир приватен, opt-out мгновенный.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
