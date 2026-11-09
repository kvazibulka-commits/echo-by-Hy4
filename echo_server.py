#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ЭХО (Echo Radar) — Сервер музыкального присутствия (echo_server.py)
Философия: «Ты видишь не людей вокруг. Ты видишь музыку, которая сейчас живет вокруг тебя».

Ключевые возможности:
- Оптимизированный SQLite в режиме WAL + busy_timeout (без блокировок при параллельных запросах)
- Двусторонняя синхронизация myColorHex, myReactions, incomingBanner и senderId
- Музыкальные тайники локаций (B2B без рекламы) и Звуковой туман кварталов (Ambient Audio Fog)
- «Призрачные следы» (Ghost Echoes) с авто-очисткой через 90 минут
- Сенсорный сонар и карта на /app с кнопкой сборки плейлиста вечера
- Автоматический UDP-маяк автопоиска (порт 8888)
"""

import hashlib
import hmac
import json
import math
import os
import random
import re
import socket
import sqlite3
import threading
import time
import urllib.parse

from collections import OrderedDict

# Только атмосферные символы настроения из Конституции. Всё остальное — оценка человека.
ALLOWED_MOOD_SYMBOLS = frozenset({"🦇", "🌑", "🌊", "⚔", "🌲", "🎼", "☁"})
# Идентификатор устройства не может содержать кавычки/разметку: он попадает в DOM и в SQL.
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_BODY_BYTES = 64 * 1024
HISTORY_TTL_SEC = 24 * 60 * 60

class LRUCache:
    def __init__(self, maxsize=500):
        self._cache = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.RLock()
    def __contains__(self, key):
        with self._lock:
            return key in self._cache
    def __getitem__(self, key):
        with self._lock:
            self._cache.move_to_end(key)
            return self._cache[key]
    def __setitem__(self, key, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTTP_PORT = int(os.environ.get("PORT", 8080))
UDP_DISCOVERY_PORT = 8888
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echo_memory.db")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "echo_config.json")

DB_LOCK = threading.Lock()
METADATA_CACHE = LRUCache(maxsize=500)
AI_PERSONA_CACHE = LRUCache(maxsize=500)
RECENT_REACTIONS = []
_PERSONA_EXECUTOR = None

ZONE_COORDINATES = {
    "Вечерняя улица": (55.7602, 37.6185),            # Камергерский / Кузнецкий Мост
    "Сретенка · Камерные бары": (55.7685, 37.6324),   # Переулки Сретенки
    "Парк Горького · Набережная": (55.7294, 37.6022), # Крымская набережная / парк
    "Покровка · Ночные кофейни": (55.7588, 37.6482),  # Покровка / Хохловка
    "Северный лесопарк": (55.8305, 37.5310),         # Тимирязевский / парковый пояс
}
DEFAULT_COORDS = (55.7558, 37.6176)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def ensure_opaque_color(val: int) -> int:
    rgb = int(val) & 0x00FFFFFF
    return 0xFF38BDF8 if rgb == 0 else (rgb | 0xFF000000)


def get_zone_jittered_coords(zone_name: str, seed_str: str):
    anchor = ZONE_COORDINATES.get(zone_name, DEFAULT_COORDS)
    h = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest()[:8], 16)
    lat_jitter = ((h % 360) - 180) / 100000.0
    lon_jitter = (((h >> 8) % 360) - 180) / 60000.0
    return round(anchor[0] + lat_jitter, 6), round(anchor[1] + lon_jitter, 6)


CITY_ZONES = [
    {
        "placeName": "Вечерняя улица",
        "dominantVibe": "Бархатный синт и теплый инди",
        "recentArtists": "Sirotkin, Pompeya, Кино",
        "defaultColor": 0xFF38BDF8,
        "listenersCount": 5
    },
    {
        "placeName": "Сретенка · Камерные бары",
        "dominantVibe": "Полуночный пост-панк и винил",
        "recentArtists": "Молчат Дома, Ploho, Дурной Вкус",
        "defaultColor": 0xFFA855F7,
        "listenersCount": 6
    },
    {
        "placeName": "Парк Горького · Набережная",
        "dominantVibe": "Дрим-поп и светлая меланхолия",
        "recentArtists": "Моя Мишель, Zoloto, Тося Чайкина",
        "defaultColor": 0xFF34D399,
        "listenersCount": 4
    },
    {
        "placeName": "Покровка · Ночные кофейни",
        "dominantVibe": "Лоу-фай, нео-соул и теплый грув",
        "recentArtists": "Антоха МС, Lovanda, Cream Soda",
        "defaultColor": 0xFFFBBF24,
        "listenersCount": 3
    },
    {
        "placeName": "Северный лесопарк",
        "dominantVibe": "Акустический эмбиент и ночной рок",
        "recentArtists": "Сплин, Би-2, Земфира",
        "defaultColor": 0xFF818CF8,
        "listenersCount": 2
    }
]

# Персональные архетипы: (образ, семья настроения, наблюдение)
ORGANIC_PERSONAS = [
    ("Архивариус забытых кассет", "vinyl_warm", "Слушает пленочные реверберации, превращая шум проспектов в медленный кадр кино."),
    ("Ловец виниловых волн", "vinyl_warm", "Собирает тепло ламповых записей и легкую ностальгию уходящего вечера."),
    ("Диспетчер ночного эфира", "synth_cold", "Ловит холодные синт-волны между фонарями, настраивая город как ненастроенный приемник."),
    ("Меланхоличный космонавт", "synth_cold", "Наблюдает за огнями ночного города как за далекими созвездиями из иллюминатора."),
    ("Хранитель северных лесов", "forest_acoustic", "В его наушниках шумит ветер среди высоких сосен и потрескивает ночной костер."),
    ("Садовник мокрых кленов", "forest_acoustic", "Прислушивается к деревянным струнам там, где роса уже осела на листьях."),
    ("Проводник туманов", "night_ambient", "Ведет невидимую тропу сквозь сумерки под медленные переливы гитарного дилея."),
    ("Гидроакустик тихих глубин", "night_ambient", "Слушает длинные эмбиент-волны так, будто сканирует дно ночного залива."),
    ("Пилигрим неоновых панелек", "neon_punk", "Вслушивается в упругий бас пустых дворов, храня верность честному звуку."),
    ("Панковый эхо-призрак дворов", "neon_punk", "Носит с собой реверберацию гаражных репетиций и честную злость ранних демо."),
    ("Фотограф вечерней воды", "dream_water", "Идет вдоль воды, где каждый гитарный аккорд отражается в темной ряби реки."),
    ("Странник сонных набережных", "dream_water", "Растворяет шаги в дрим-поп дымке, где город кажется проявленной пленкой."),
    ("Бармен дымного света", "jazz_smoke", "Подбирает аккорды под шорох льда в стакане и медленный свет торшеров."),
    ("Алхимик забытых мелодий", "jazz_smoke", "Превращает простую прогулку по переулкам в чистое поэтическое переживание."),
    ("Созерцатель тихих чердаков", "lofi_rain", "Ловит неспешные клавишные аккорды под мягкий стук редких капель дождя по жести."),
    ("Штурман полуночных мостов", "lofi_rain", "Синхронизирует дыхание с плотным кинематографичным битом ночного мегаполиса."),
    ("Дозорный пустых проспектов", "neon_punk", "Видит город чётче всего, когда на улицах никого."),
    ("Летописец серых рассветов", "neon_punk", "Каждый рассвет записывает, но никому не показывает."),
    ("Смотритель медленных волн", "night_ambient", "Следит, чтобы волны не спешили."),
    ("Картограф тишины", "night_ambient", "Наносит на карту места, где можно услышать себя."),
    ("Сигнальщик ночных поездов", "vinyl_warm", "Машет фонарём тем, кто едет в ту же ночь."),
    ("Хранитель длинных проигрышей", "vinyl_warm", "Верит, что главное начинается после второго куплета."),
    ("Кузнец зимних гроз", "forest_acoustic", "Куёт медленные риффы, как якоря."),
    ("Странник у погасших костров", "forest_acoustic", "Находит тепло там, где уже почти погасло."),
    ("Ночной лоцман города", "synth_cold", "Проводит через перекрёстки, не касаясь асфальта."),
    ("Оператор вечерних огней", "synth_cold", "Включает витрины в такт басу."),
    ("Собиратель городских эхо", "synth_cold", "Складывает отражения звука в карманы."),
    ("Тайный бармен поздних баров", "jazz_smoke", "Наливает молча, но всегда в тему."),
    ("Собиратель неспешных соло", "jazz_smoke", "Умеет слушать паузу между нотами."),
    ("Ди-джей дождливых витрин", "jazz_smoke", "Подбирает пластинки под ритм капель."),
    ("Бит-скиталец спальных районов", "lofi_rain", "Знает наизусть звук каждой остановки."),
    ("Летописец бетонных дворов", "lofi_rain", "Записывает город так, как он звучит снизу."),
    ("Флейтист подземных переходов", "lofi_rain", "Настраивает эхо арки на свой лад."),
    ("Хранитель лунного света", "night_ambient", "Выдаёт его только тем, кто не спешит."),
    ("Тихий архивариус тишины", "night_ambient", "Подшивает паузы в папки с вечерами."),
    ("Слушатель полуночных зал", "night_ambient", "Пока все спят — слушает, как дышит дом."),
    ("Хранитель северных троп", "forest_acoustic", "Помнит, каким поют на перевалах."),
    ("Сказитель вечерних костров", "forest_acoustic", "Рассказывает только то, что спето."),
    ("Пастух утренних туманов", "forest_acoustic", "Выводит туманы на пастбище до рассвета."),
    ("Коллекционер воздушных шаров", "dream_water", "Держит лёгкое на привязи."),
    ("Ловец золотых сумерек", "dream_water", "Ловит сумерки сачком из наушников."),
    ("Флорист неоновых витрин", "dream_water", "Составляет букеты из вечерних вывесок.")
]


MOOD_FAMILIES = {
    "vinyl_warm": ("Тёплый винил", 0xFFFBBF24),
    "synth_cold": ("Холодный синт", 0xFF38BDF8),
    "forest_acoustic": ("Северная акустика", 0xFF34D399),
    "night_ambient": ("Ночной эмбиент", 0xFF818CF8),
    "neon_punk": ("Неоновый пост-панк", 0xFFA855F7),
    "dream_water": ("Дрим у воды", 0xFFF472B6),
    "jazz_smoke": ("Дымный джаз", 0xFF2DD4BF),
    "lofi_rain": ("Лоу-фай дождь", 0xFF94A3B8),
}

# Ключевые слова жанра → семья настроения (для детерминированного фолбэка)
GENRE_FAMILY_HINTS = [
    (("jazz", "джаз", "swing", "свинг", "soul", "соул", "blues"), "jazz_smoke"),
    (("punk", "панк", "post-punk", "no wave", "wave", "гот", "goth"), "neon_punk"),
    (("ambient", "эмбиент", "drone", "dron", "classical", "классик", "new age"), "night_ambient"),
    (("folk", "фолк", "acoustic", "акустик", "country", "кантри"), "forest_acoustic"),
    (("dream", "дрим", "shoegaze", "шугейз", "ethereal"), "dream_water"),
    (("lo-fi", "lofi", "low-fi", "chill", "чилл", "trip-hop", "triphop", "downtempo"), "lofi_rain"),
    (("synth", "синт", "electro", "электро", "techno", "техно", "idm", "house"), "synth_cold"),
    (("indie", "инди", "rock", "рок", "alternative", "альтернатива", "pop", "поп", "vinyl", "винил", "metal", "метал"), "vinyl_warm"),
]


def family_from_genre(genre: str, seed_digest: bytes) -> str:
    """Семья настроения по жанру; если жанр неизвестен — по хешу трека."""
    g = (genre or "").lower()
    if g:
        for keys, fam in GENRE_FAMILY_HINTS:
            for key in keys:
                if key in g:
                    return fam
    families = list(MOOD_FAMILIES.keys())
    return families[seed_digest[0] % len(families)]


def clamp_int(value, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except Exception:
        return lo


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"gemini_api_key": os.environ.get("GEMINI_API_KEY", "")}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


SERVER_CONFIG = load_config()

# Токен API: если задан, все запросы (GET и POST) требуют заголовок X-API-Key.
# Секрет НИКОГДА не подставляется в HTML: клиент хранит его у себя.
API_TOKEN = os.environ.get("ECHO_API_TOKEN", "").strip()


def request_has_valid_token(headers) -> bool:
    """Постоянное по времени сравнение; без токена режим локальной сети остаётся открытым."""
    if not API_TOKEN:
        return True
    supplied = (headers.get("X-API-Key") or headers.get("X-Echo-Token") or "").strip()
    return hmac.compare_digest(supplied, API_TOKEN)


def safe_device_id(raw) -> str:
    """Валидация идентификатора: только [A-Za-z0-9_-], иначе генерируем одноразовый."""
    value = str(raw or "").strip()
    if DEVICE_ID_RE.fullmatch(value):
        return value
    return "dev_" + hashlib.md5((value + str(time.time_ns())).encode("utf-8")).hexdigest()[:12]


def safe_number(raw, fallback, cast, lo, hi):
    """Число из недоверенного JSON без ValueError и без выхода за границы."""
    try:
        value = cast(raw)
        if math.isnan(value) or math.isinf(value):
            return fallback
        return min(hi, max(lo, value))
    except (TypeError, ValueError, OverflowError):
        return fallback


def distance_bucket(meters: int) -> str:
    """Только крупные диапазоны: ответ API не содержит точного расстояния."""
    if meters < 80:
        return "near"
    if meters < 180:
        return "medium"
    return "far"


def bucket_meters(meters: int) -> int:
    """Грубое значение для старых клиентов, чтобы не сломать подпись «~N м»."""
    return {"near": 50, "medium": 150, "far": 300}[distance_bucket(meters)]


def get_gemini_key() -> str:
    """Куратор по умолчанию — локальная математика, без платных зарубежных API.

    Внешний ИИ включается только явным opt-in ECHO_ALLOW_EXTERNAL_AI=1:
    тогда ключ берётся из окружения, а не из файла в репозитории.
    """
    if os.environ.get("ECHO_ALLOW_EXTERNAL_AI", "").strip() != "1":
        return ""
    return os.environ.get("GEMINI_API_KEY", "").strip()

import concurrent.futures
_PERSONA_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="echo_persona")


def get_local_ip():
    try:
        hostname = socket.gethostname()
        all_ips = socket.gethostbyname_ex(hostname)[2]
        for ip in all_ips:
            if ip.startswith("192.168.") or ip.startswith("172.16.") or ip.startswith("172.20."):
                return ip
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def init_db():
    with DB_LOCK:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                artist TEXT,
                title TEXT,
                source_name TEXT,
                persona_title TEXT,
                persona_observation TEXT,
                mood_label TEXT,
                color_hex INTEGER,
                avatar_petals INTEGER,
                zone_name TEXT,
                distance_meters INTEGER,
                angle_degrees REAL,
                distance_ratio REAL,
                compatibility_percent INTEGER,
                resonance_days INTEGER DEFAULT 1,
                encounters_count INTEGER DEFAULT 1,
                reactions_json TEXT DEFAULT '{}',
                lat REAL,
                lon REAL,
                updated_at REAL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS listening_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                artist TEXT,
                title TEXT,
                persona_title TEXT,
                zone_name TEXT,
                played_at TEXT,
                timestamp REAL DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS capsules (
                id TEXT PRIMARY KEY,
                creator_id TEXT,
                artist TEXT,
                title TEXT,
                mood_label TEXT,
                persona_title TEXT,
                zone_name TEXT,
                secret_message TEXT,
                distance_meters INTEGER,
                angle_degrees REAL,
                distance_ratio REAL,
                compatibility_percent INTEGER,
                is_unlocked INTEGER DEFAULT 1,
                lat REAL,
                lon REAL,
                is_venue INTEGER DEFAULT 0,
                venue_name TEXT DEFAULT '',
                created_at REAL
            )
        """)
        conn.commit()

        cur.execute("PRAGMA table_info(devices)")
        existing_cols = {row[1] for row in cur.fetchall()}
        cols_to_add = {
            "source_name": "TEXT DEFAULT 'ЭХО'",
            "persona_title": "TEXT DEFAULT ''",
            "persona_observation": "TEXT DEFAULT ''",
            "mood_label": "TEXT DEFAULT ''",
            "color_hex": "INTEGER DEFAULT 4281843192",
            "avatar_petals": "INTEGER DEFAULT 6",
            "zone_name": "TEXT DEFAULT 'Вечерняя улица'",
            "distance_meters": "INTEGER DEFAULT 60",
            "angle_degrees": "REAL DEFAULT 45.0",
            "distance_ratio": "REAL DEFAULT 0.45",
            "compatibility_percent": "INTEGER DEFAULT 85",
            "resonance_days": "INTEGER DEFAULT 1",
            "encounters_count": "INTEGER DEFAULT 1",
            "reactions_json": "TEXT DEFAULT '{}'",
            "lat": "REAL DEFAULT 55.7602",
            "lon": "REAL DEFAULT 37.6185",
            "updated_at": "REAL DEFAULT 0"
        }
        for cname, ctype in cols_to_add.items():
            if cname not in existing_cols:
                try:
                    cur.execute(f"ALTER TABLE devices ADD COLUMN {cname} {ctype}")
                except Exception:
                    pass

        cur.execute("PRAGMA table_info(listening_history)")
        hist_cols = {row[1] for row in cur.fetchall()}
        if "timestamp" not in hist_cols:
            try:
                cur.execute("ALTER TABLE listening_history ADD COLUMN timestamp REAL DEFAULT 0")
            except Exception:
                pass

        cur.execute("PRAGMA table_info(capsules)")
        cap_cols = {row[1] for row in cur.fetchall()}
        if "lat" not in cap_cols:
            try:
                cur.execute("ALTER TABLE capsules ADD COLUMN lat REAL DEFAULT 55.7602")
                cur.execute("ALTER TABLE capsules ADD COLUMN lon REAL DEFAULT 37.6185")
            except Exception:
                pass
        if "is_venue" not in cap_cols:
            try:
                cur.execute("ALTER TABLE capsules ADD COLUMN is_venue INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE capsules ADD COLUMN venue_name TEXT DEFAULT ''")
            except Exception:
                pass
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM devices")
        if cur.fetchone()[0] == 0 and os.environ.get("ECHO_SEED_DEMO") == "1":
            now = time.time()
            s1_lat, s1_lon = get_zone_jittered_coords("Вечерняя улица", "seed1")
            s2_lat, s2_lon = get_zone_jittered_coords("Сретенка · Камерные бары", "seed2")
            s3_lat, s3_lon = get_zone_jittered_coords("Парк Горького · Набережная", "seed3")

            seed_signals = [
                (
                    "sig_sirotkin_01", "Sirotkin", "Август", "Яндекс Музыка",
                    "Архивариус забытых кассет",
                    "Слушает теплые гитарные реверберации, превращая суету улицы в медленный ночной фильм.",
                    "Теплый винил", 0xFF38BDF8, 6, "Вечерняя улица", 38, 35.0, 0.32, 94, 3, 4,
                    json.dumps({"🌑": 4, "🌊": 2}, ensure_ascii=False), s1_lat, s1_lon, now
                ),
                (
                    "sig_molchat_02", "Молчат Дома", "Судно", "VK Музыка",
                    "Пилигрим неоновых панелек",
                    "Вслушивается в упругий бас пустых переулков, удерживая пульс вечернего города.",
                    "Холодный пост-панк", 0xFFA855F7, 8, "Сретенка · Камерные бары", 75, 145.0, 0.52, 88, 2, 3,
                    json.dumps({"🌲": 3, "🌊": 1}, ensure_ascii=False), s2_lat, s2_lon, now
                ),
                (
                    "sig_pompeya_03", "Pompeya", "90", "Яндекс Музыка",
                    "Ловец виниловых волн",
                    "Несет в наушниках солнечный дрим-грув и легкую ностальгию уходящего дня.",
                    "Бархатный инди", 0xFF34D399, 5, "Парк Горького · Набережная", 115, 255.0, 0.68, 81, 1, 1,
                    json.dumps({"🎼": 2}, ensure_ascii=False), s3_lat, s3_lon, now
                )
            ]
            cur.executemany("""
                INSERT OR REPLACE INTO devices (
                    id, artist, title, source_name, persona_title, persona_observation,
                    mood_label, color_hex, avatar_petals, zone_name, distance_meters,
                    angle_degrees, distance_ratio, compatibility_percent, resonance_days,
                    encounters_count, reactions_json, lat, lon, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, seed_signals)
            conn.commit()

        cur.execute("SELECT COUNT(*) FROM capsules")
        if cur.fetchone()[0] == 0 and os.environ.get("ECHO_SEED_DEMO") == "1":
            now = time.time()
            c1_lat, c1_lon = get_zone_jittered_coords("Вечерняя улица", "cap1")
            c2_lat, c2_lon = get_zone_jittered_coords("Вечерняя улица", "cap2")
            c3_lat, c3_lon = get_zone_jittered_coords("Сретенка · Камерные бары", "cap3")
            seed_caps = [
                (
                    "cap_seed_01", "echo_curator", "Кино", "Спокойная ночь",
                    "Холодная акустика", "Проводник туманов", "Вечерняя улица",
                    "Слушай этот трек, когда огни фонарей начнут двоиться в мокром стекле...",
                    52, 50.0, 0.36, 95, 1, c1_lat, c1_lon, 0, "", now
                ),
                (
                    "cap_seed_02", "echo_curator", "Sirotkin", "Выше домов",
                    "Теплый винил", "Архивариус забытых кассет", "Вечерняя улица",
                    "Остановись на перекрестке и подними глаза к крышам домов.",
                    95, 215.0, 0.55, 91, 1, c2_lat, c2_lon, 0, "", now
                ),
                (
                    "cap_venue_03", "venue_vinyl_lab", "Massive Attack", "Teardrop",
                    "Аналоговый трип-хоп", "Виниловая лавка «Фонограф»", "Сретенка · Камерные бары",
                    "Сегодня здесь звучали шорох винила, тихий джаз и дождь за окном.",
                    70, 160.0, 0.42, 97, 1, c3_lat, c3_lon, 1, "Виниловая лавка «Фонограф»", now
                )
            ]
            cur.executemany("""
                INSERT INTO capsules (
                    id, creator_id, artist, title, mood_label, persona_title,
                    zone_name, secret_message, distance_meters, angle_degrees,
                    distance_ratio, compatibility_percent, is_unlocked, lat, lon,
                    is_venue, venue_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, seed_caps)
            conn.commit()

        conn.close()


def lookup_metadata(artist: str, title: str):
    key = f"{artist.strip().lower()}::{title.strip().lower()}"
    if key in METADATA_CACHE:
        return METADATA_CACHE[key]

    meta = {"album": "", "genre": ""}
    query = urllib.parse.quote(f"{artist} {title}".strip())
    url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1&country=RU"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EchoRadar/3.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            items = data.get("results", [])
            if items:
                meta["album"] = items[0].get("collectionName", "")
                meta["genre"] = items[0].get("primaryGenreName", "")
    except Exception:
        pass

    # MusicBrainz — второй источник (бесплатный, без ключа), если iTunes промолчал
    if not meta["album"] or not meta["genre"]:
        try:
            clean_artist = artist.replace('"', "").strip()
            clean_title = title.replace('"', "").strip()
            mb_query = urllib.parse.quote(f'recording:"{clean_title}" AND artist:"{clean_artist}"')
            mb_url = f"https://musicbrainz.org/ws/2/recording?query={mb_query}&limit=1&fmt=json"
            mb_req = urllib.request.Request(
                mb_url,
                headers={"User-Agent": "EchoRadar/3.0 (echo-radar; echo@example.com)"}
            )
            with urllib.request.urlopen(mb_req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                recs = data.get("recordings") or []
                if recs:
                    rec = recs[0]
                    if not meta["album"]:
                        releases = rec.get("releases") or []
                        if releases:
                            meta["album"] = str(releases[0].get("title") or "")
                    if not meta["genre"]:
                        tags = rec.get("tags") or []
                        if tags:
                            meta["genre"] = str(tags[0].get("name") or "").capitalize()
        except Exception:
            pass

    METADATA_CACHE[key] = meta
    return meta


def get_recent_tracks_for_device(device_id: str, limit: int = 5):
    try:
        with DB_LOCK:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT artist, title FROM listening_history WHERE device_id = ? ORDER BY id DESC LIMIT ?",
                (device_id, limit)
            )
            rows = cur.fetchall()
            conn.close()
            return [f"{r[0]} — {r[1]}" for r in rows]
    except Exception:
        return []


GEMINI_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]

PERSONA_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "personaTitle": {"type": "STRING"},
        "moodFamily": {"type": "STRING", "enum": list(MOOD_FAMILIES.keys())},
        "personaObservation": {"type": "STRING"},
        "avatarPetals": {"type": "INTEGER"},
        "compatibilityPercent": {"type": "INTEGER"}
    },
    "required": ["personaTitle", "moodFamily", "personaObservation", "avatarPetals", "compatibilityPercent"]
}


def generate_persona_via_gemini(api_key: str, artist: str, title: str, album: str,
                                genre: str, source_name: str, recent_tracks: list):
    family_hints = "; ".join(
        f"{fam} = {label.lower()}" for fam, (label, _hex) in MOOD_FAMILIES.items()
    )
    prompt_text = (
        f"Ты — чуткий музыкальный куратор и поэт атмосферного приложения 'ЭХО'.\n"
        f"Твоя задача — создать глубокий, естественный и уникальный литературный образ слушателя без механических штампов.\n"
        f"Текущий трек: '{artist} — {title}'.\n"
        f"Альбом: '{album or 'Сингл'}'. Жанр: '{genre or 'неизвестен'}'. Плеер: '{source_name or 'неизвестен'}'.\n"
        f"Недавние прослушивания за вечер: {recent_tracks or 'Начало пути'}.\n\n"
        f"Правила:\n"
        f"1. personaTitle — поэтичный образ слушателя из 2-4 слов на русском языке, отражающий жанр, альбом и настроение трека (например: Архивариус забытых кассет, Проводник туманов, Хранитель северных лесов).\n"
        f"2. moodFamily — выбери ровно одну семью настроения, лучше всего описывающую текстуру звука. Доступные семьи: {family_hints}.\n"
        f"3. personaObservation — 1-2 глубоких предложения о том, как этот трек звучит в вечернем городе; естественно упомяни жанр или инструменты.\n"
        f"4. avatarPetals — целое число 4-9: сколько 'лепестков' у ауры этого звучания.\n"
        f"5. compatibilityPercent — целое число 70-97: насколько трек вписывается в вечерний вайб.\n"
        f"Верни только JSON-объект без markdown-разметки."
    )

    for model_name in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key.strip()}"
        req_data = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "temperature": 0.8,
                "maxOutputTokens": 450,
                "responseMimeType": "application/json",
                "responseSchema": PERSONA_RESPONSE_SCHEMA
            }
        }
        body_bytes = json.dumps(req_data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                resp_json = json.loads(resp.read().decode("utf-8", errors="ignore"))
                candidates = resp_json.get("candidates", [])
                if not candidates:
                    continue
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    continue
                raw_text = parts[0].get("text", "").strip()
                cleaned = re.sub(r"```json|```", "", raw_text).strip()
                data = json.loads(cleaned)

                family = str(data.get("moodFamily", ""))
                if family not in MOOD_FAMILIES:
                    seed_str = f"{artist}|{album}|{title}".lower()
                    digest = hashlib.sha256(seed_str.encode("utf-8")).digest()
                    family = family_from_genre(genre, digest)
                mood_label, fam_color = MOOD_FAMILIES[family]

                return {
                    "personaTitle": str(data.get("personaTitle", "Архивариус забытых кассет"))[:60],
                    "moodLabel": mood_label,
                    "personaObservation": str(data.get("personaObservation", "Слушает музыку в вечерней тишине.")),
                    "colorHex": ensure_opaque_color(fam_color),
                    "avatarPetals": clamp_int(data.get("avatarPetals", 6), 4, 9),
                    "compatibilityPercent": clamp_int(data.get("compatibilityPercent", 85), 70, 97)
                }
        except Exception:
            continue
    return None


def generate_curated_persona(artist: str, title: str, device_id: str,
                             incoming_recent_tracks: list = None,
                             source_name: str = "", use_ai: bool = True):
    cache_key = f"{artist.strip().lower()}::{title.strip().lower()}"
    if cache_key in AI_PERSONA_CACHE:
        return AI_PERSONA_CACHE[cache_key]

    meta = lookup_metadata(artist, title)
    album = meta.get("album", "")
    genre = meta.get("genre", "")
    recent_tracks = incoming_recent_tracks or get_recent_tracks_for_device(device_id, limit=4)

    api_key = get_gemini_key() if use_ai else ""
    if api_key:
        ai_res = generate_persona_via_gemini(api_key, artist, title, album, genre, source_name, recent_tracks)
        if ai_res:
            AI_PERSONA_CACHE[cache_key] = ai_res
            return ai_res

    # Детерминированный фолбэк: учитывает жанр, артиста, альбом и трек
    seed_str = f"{artist.lower().strip()}|{album.lower().strip()}|{title.lower().strip()}"
    digest = hashlib.sha256(seed_str.encode("utf-8")).digest()

    family = family_from_genre(genre, digest)
    mood_label, fam_color = MOOD_FAMILIES[family]
    pool = [p for p in ORGANIC_PERSONAS if p[1] == family] or ORGANIC_PERSONAS
    title_base, _fam, obs_base = pool[digest[1] % len(pool)]

    petals = 4 + (digest[2] % 6)      # 4..9
    compat = 70 + (digest[3] % 28)    # 70..97

    obs = obs_base
    if genre:
        obs = f"{obs} [Жанровая ткань: {genre}]"
    if len(recent_tracks) > 1:
        obs = f"{obs} [Траектория вечера: путь сквозь {len(recent_tracks)} композиции к ночному созерцанию]"

    result = {
        "personaTitle": title_base,
        "moodLabel": mood_label,
        "personaObservation": obs,
        "colorHex": ensure_opaque_color(fam_color),
        "avatarPetals": petals,
        "compatibilityPercent": compat
    }
    AI_PERSONA_CACHE[cache_key] = result
    return result


def apply_persona_to_device(device_id: str, artist: str, title: str, curated: dict):
    """Дозаписывает готовую персону в устройства (не затирая более новый трек)."""
    try:
        with DB_LOCK:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE devices
                SET persona_title = ?, persona_observation = ?, mood_label = ?,
                    color_hex = ?, avatar_petals = ?, compatibility_percent = ?
                WHERE id = ? AND artist = ? AND title = ?
                """,
                (
                    str(curated["personaTitle"]),
                    str(curated["personaObservation"]),
                    str(curated["moodLabel"]),
                    ensure_opaque_color(int(curated["colorHex"])),
                    clamp_int(curated["avatarPetals"], 4, 9),
                    clamp_int(curated["compatibilityPercent"], 70, 97),
                    device_id, artist, title
                )
            )
            conn.commit()
            conn.close()
    except Exception:
        pass


def _generate_and_apply_persona(device_id: str, artist: str, title: str,
                                incoming_recent: list, source_name: str):
    """Фоновая генерация персоны: сигнал уже в эфире, образ догоняет его."""
    try:
        curated = generate_curated_persona(
            artist, title, device_id,
            incoming_recent_tracks=incoming_recent,
            source_name=source_name,
            use_ai=True
        )
        apply_persona_to_device(device_id, artist, title, curated)
    except Exception:
        pass


def record_history_if_unique(cur, device_id: str, artist: str, title: str, persona_title: str, zone_name: str):
    cur.execute(
        "SELECT artist, title FROM listening_history WHERE device_id = ? ORDER BY id DESC LIMIT 1",
        (device_id,)
    )
    last = cur.fetchone()
    if last and (last[0] or "").strip().lower() == artist.strip().lower() \
            and (last[1] or "").strip().lower() == title.strip().lower():
        return

    now_ts = time.time()
    now_str = datetime.now().strftime("%H:%M")
    cur.execute(
        """
        INSERT INTO listening_history (device_id, artist, title, persona_title, zone_name, played_at, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (device_id, artist, title, persona_title, zone_name, now_str, now_ts)
    )


def build_state_payload(for_device_id: str = None):
    with DB_LOCK:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        now_sec = time.time()

        # Автоматическая очистка старых треков старше 90 минут и истории старше суток.
        # История не должна жить вечно: «Невидимка» не оставляет следов в эфире.
        cutoff_time = now_sec - (90 * 60)
        cur.execute("DELETE FROM devices WHERE updated_at < ?", (cutoff_time,))
        cur.execute("DELETE FROM listening_history WHERE timestamp < ?", (now_sec - HISTORY_TTL_SEC,))
        conn.commit()

        cur.execute("SELECT * FROM devices ORDER BY updated_at DESC LIMIT 35")
        dev_rows = cur.fetchall()

        signals = []
        zone_buckets = {}
        my_device_record = None

        for row in dev_rows:
            dev_id = row["id"]
            if for_device_id and dev_id == for_device_id:
                my_device_record = row

            try:
                reactions = json.loads(row["reactions_json"] or "{}")
            except Exception:
                reactions = {}

            updated_at = float(row["updated_at"] or 0)
            age_min = max(0, int((now_sec - updated_at) / 60.0))
            is_ghost = age_min > 5

            sig = {
                "id": dev_id,
                "artist": row["artist"],
                "title": row["title"],
                "sourceName": row["source_name"] or "ЭХО",
                "distanceBucket": distance_bucket(int(row["distance_meters"] or 50)),
                "distanceMeters": bucket_meters(int(row["distance_meters"] or 50)),
                "angleDegrees": float(row["angle_degrees"] or 45.0),
                "distanceRatio": float(row["distance_ratio"] or 0.45),
                "personaTitle": row["persona_title"] or "Архивариус забытых кассет",
                "personaObservation": (
                    f"Призрачный след: звучал {age_min} мин назад. {row['persona_observation']}"
                    if is_ghost else (row["persona_observation"] or "Слушает музыку в вечерней тишине.")
                ),
                "moodLabel": (f"Призрачный след ({age_min} мин)" if is_ghost else (row["mood_label"] or "Теплый винил")),
                "compatibilityPercent": int(row["compatibility_percent"] or 88),
                "resonanceDays": int(row["resonance_days"] or 1),
                "encountersCount": int(row["encounters_count"] or 1),
                "colorHex": ensure_opaque_color(int(row["color_hex"] or 0xFF38BDF8)),
                "avatarPetals": int(row["avatar_petals"] or 6),
                "zoneName": row["zone_name"] or "Вечерняя улица",
                "updatedAt": updated_at,
                "isGhost": is_ghost,
                "ageMinutes": age_min,
                "reactions": reactions
            }
            signals.append(sig)
            zone_buckets.setdefault(sig["zoneName"], []).append(sig)

        cur.execute("SELECT * FROM capsules ORDER BY created_at DESC LIMIT 30")
        cap_rows = cur.fetchall()
        capsules = []
        for crow in cap_rows:
            is_ven = bool(crow["is_venue"]) if "is_venue" in crow.keys() else False
            ven_name = crow["venue_name"] if "venue_name" in crow.keys() else ""
            capsules.append({
                "id": crow["id"],
                "creatorId": crow["creator_id"],
                "artist": crow["artist"],
                "title": crow["title"],
                "moodLabel": crow["mood_label"],
                "personaTitle": crow["persona_title"],
                "zoneName": crow["zone_name"],
                "secretMessage": crow["secret_message"],
                "distanceMeters": int(crow["distance_meters"] or 65),
                "angleDegrees": float(crow["angle_degrees"] or 120.0),
                "distanceRatio": float(crow["distance_ratio"] or 0.5),
                "compatibilityPercent": int(crow["compatibility_percent"] or 90),
                "isUnlocked": bool(crow["is_unlocked"]),
                "isVenue": is_ven,
                "venueName": ven_name,
                "createdAt": float(crow["created_at"] or 0)
            })

        # Вечерний дневник показываем только самому себе: чужая история прослушиваний
        # не должна попадать в общий ответ радара.
        if for_device_id:
            cur.execute(
                "SELECT artist, title, persona_title, zone_name, played_at FROM listening_history "
                "WHERE device_id = ? ORDER BY id DESC LIMIT 15",
                (for_device_id,)
            )
            hist_rows = cur.fetchall()
        else:
            hist_rows = []
        evening_traces = []
        for h in hist_rows:
            evening_traces.append(
                f"[{h['played_at']}] {h['persona_title']} ({h['zone_name']}): {h['artist']} — {h['title']}"
            )

        if not evening_traces:
            evening_traces = [
                "В эфире зажглись первые вечерние маяки.",
                "Сонар сканирует виниловые ауры в радиусе тишины..."
            ]

        city_zones = []
        for z in CITY_ZONES:
            zname = z["placeName"]
            bucket = zone_buckets.get(zname, [])
            center_coords = ZONE_COORDINATES.get(zname, DEFAULT_COORDS)

            if bucket:
                artists_str = ", ".join(list(dict.fromkeys(b["artist"] for b in bucket))[:4])
                vibe_str = bucket[0]["moodLabel"]
                dominant_color = bucket[0]["colorHex"]
                count_val = len(bucket)
                fog_radius = min(750, 480 + (len(bucket) * 60))
            else:
                artists_str = z["recentArtists"]
                vibe_str = z["dominantVibe"]
                dominant_color = z.get("defaultColor", 0xFF38BDF8)
                count_val = 0
                fog_radius = 450

            city_zones.append({
                "placeName": zname,
                "dominantVibe": vibe_str,
                "recentArtists": artists_str,
                "listenersCount": count_val,
                "lat": center_coords[0],
                "lon": center_coords[1],
                "fogColorHex": ensure_opaque_color(dominant_color),
                "fogRadius": fog_radius
            })

        active_banner = None
        if for_device_id:
            # Баннер видит только устройство-адресат
            for r in reversed(RECENT_REACTIONS):
                if now_sec - r["time"] < 14.0 and r["targetId"] == for_device_id:
                    active_banner = {
                        "id": r["id"],
                        "targetId": r["targetId"],
                        "senderId": r["senderId"],
                        "symbol": r["symbol"],
                        "message": r["message"]
                    }
                    break

        my_reactions = {}
        my_refined_mood = ""
        my_curated_persona = ""
        my_curated_obs = ""
        my_color_hex = 0xFF38BDF8
        my_avatar_petals = 6

        if my_device_record:
            try:
                my_reactions = json.loads(my_device_record["reactions_json"] or "{}")
            except Exception:
                my_reactions = {}
            my_refined_mood = my_device_record["mood_label"] or ""
            my_curated_persona = my_device_record["persona_title"] or ""
            my_curated_obs = my_device_record["persona_observation"] or ""
            my_color_hex = ensure_opaque_color(int(my_device_record["color_hex"] or 0xFF38BDF8))
            my_avatar_petals = clamp_int(my_device_record["avatar_petals"] or 6, 4, 9)

        conn.close()

    return {
        "status": "ok",
        "serverTime": int(now_sec),
        "signals": signals,
        "capsules": capsules,
        "eveningTraces": evening_traces,
        "cityZones": city_zones,
        "incomingBanner": active_banner,
        "myReactions": my_reactions,
        "myRefinedMood": my_refined_mood,
        "myCuratedPersona": my_curated_persona,
        "myCuratedObservation": my_curated_obs,
        "myColorHex": my_color_hex,
        "myAvatarPetals": my_avatar_petals
    }


def _load_template(name: str) -> str:
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", name)
    try:
        with open(tpl_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return f"<h1>Template {name} not found</h1>"

DASHBOARD_HTML = _load_template("dashboard.html")
PWA_HTML = _load_template("pwa.html")


def render_page(html_text: str) -> str:
    """Секрет в HTML больше не подставляется: страницы публичные, токен хранится у клиента."""
    return html_text.replace("{{ECHO_API_KEY}}", "")





class EchoRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send_json(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, X-API-Key, X-Echo-Token")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, html_text, status=200):
        raw = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; "
                         "script-src 'self' 'unsafe-inline' https://unpkg.com; "
                         "style-src 'self' 'unsafe-inline' https://unpkg.com; "
                         "img-src 'self' data: https://unpkg.com https://tile.openstreetmap.org; "
                         "connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, X-API-Key, X-Echo-Token")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Эфир содержит треки людей рядом: при включённом токене читать его может
        # только клиент, которому пользователь этот токен передал.
        if path.startswith("/api/") and not request_has_valid_token(self.headers):
            self._send_json({"status": "unauthorized"}, status=401)
            return

        if path == "/api/state":
            qs = urllib.parse.parse_qs(parsed.query)
            for_device = (qs.get("deviceId") or [""])[0].strip()
            self._send_json(build_state_payload(for_device_id=for_device or None))
        elif path in ("/", "/index.html"):
            self._send_html(render_page(DASHBOARD_HTML))
        elif path in ("/app", "/pwa"):
            self._send_html(render_page(PWA_HTML))
        else:
            self._send_json({"status": "ok", "endpoint": path})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Опциональная защита: при заданном ECHO_API_TOKEN все запросы требуют X-API-Key
        if not request_has_valid_token(self.headers):
            self._send_json({"status": "unauthorized"}, status=401)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_json({"status": "payload_too_large"}, status=413)
            return
        body_raw = self.rfile.read(length).decode("utf-8", errors="ignore") if length > 0 else "{}"
        try:
            data = json.loads(body_raw)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}

        if path == "/api/signal":
            dev_id = safe_device_id(data.get("id"))
            artist = str(data.get("artist") or "Неизвестный артист").strip()[:160]
            title = str(data.get("title") or "Без названия").strip()[:160]
            source_name = str(data.get("sourceName") or "Android ЭХО").strip()[:80]
            zone_name = str(data.get("zoneName") or "Вечерняя улица").strip()[:80]

            incoming_recent = data.get("recentTracks", [])

            # Персона считается асинхронно: сигнал появляется в эфире мгновенно,
            # а образ дозаписывается фоновым воркером — клиент подхватит его
            # следующим опросом состояния (заглушка честно это отражает).
            curated = {
                "personaTitle": "Куратор вслушивается…",
                "moodLabel": "Настройка эфира",
                "personaObservation": "ИИ-куратор подбирает образ для этого звучания.",
                "colorHex": ensure_opaque_color(0xFF38BDF8),
                "avatarPetals": 6,
                "compatibilityPercent": 85
            }
            _PERSONA_EXECUTOR.submit(
                _generate_and_apply_persona, dev_id, artist, title, incoming_recent, source_name
            )
            persona_title = str(curated["personaTitle"])
            persona_obs = str(curated["personaObservation"])
            mood_label = str(curated["moodLabel"])
            color_hex = ensure_opaque_color(int(curated["colorHex"]))
            avatar_petals = int(curated["avatarPetals"])
            compat = int(curated["compatibilityPercent"])

            h_val = int(hashlib.md5(dev_id.encode("utf-8")).hexdigest()[:8], 16)
            dist_meters = safe_number(data.get("distanceMeters"), 25 + (h_val % 180), int, 1, 500)
            angle_deg = safe_number(data.get("angleDegrees"), float(h_val % 360), float, 0.0, 360.0)
            dist_ratio = safe_number(
                data.get("distanceRatio"),
                min(0.88, max(0.22, dist_meters / 220.0)),
                float, 0.0, 1.0
            )

            lat, lon = get_zone_jittered_coords(zone_name, dev_id)

            now = time.time()
            with DB_LOCK:
                conn = get_db_connection()
                cur = conn.cursor()

                cur.execute("SELECT encounters_count, resonance_days, reactions_json FROM devices WHERE id = ?", (dev_id,))
                existing = cur.fetchone()
                enc_count = existing[0] if existing else 1
                res_days = existing[1] if existing else 1
                reactions_json = existing[2] if existing else "{}"

                cur.execute("""
                    INSERT OR REPLACE INTO devices (
                        id, artist, title, source_name, persona_title, persona_observation,
                        mood_label, color_hex, avatar_petals, zone_name, distance_meters,
                        angle_degrees, distance_ratio, compatibility_percent, resonance_days,
                        encounters_count, reactions_json, lat, lon, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    dev_id, artist, title, source_name, persona_title, persona_obs,
                    mood_label, color_hex, avatar_petals, zone_name, dist_meters,
                    angle_deg, dist_ratio, compat, res_days, enc_count, reactions_json,
                    lat, lon, now
                ))

                record_history_if_unique(cur, dev_id, artist, title, persona_title, zone_name)
                conn.commit()
                conn.close()

            print(f"[{datetime.now().strftime('%H:%M:%S')}] 📡 СИГНАЛ [{zone_name}] {dev_id}: {artist} — {title} ({persona_title} • {mood_label})")
            payload = build_state_payload(for_device_id=dev_id)
            self._send_json(payload)

        elif path == "/api/save_config":
            # Куратор работает локальной математикой на stdlib: платный зарубежный
            # API и хранение чужого ключа в конфиге проекту не нужны.
            self._send_json({"status": "disabled", "curator": "local", "has_key": False})

        elif path == "/api/react":
            target_id = safe_device_id(data.get("targetId") or data.get("signalId") or "")
            sender_id = safe_device_id(data.get("senderId") or "unknown_sender")
            symbol = str(data.get("symbol") or "🎼").strip()
            if symbol not in ALLOWED_MOOD_SYMBOLS:
                symbol = "🎼"
            if sender_id == target_id:
                self._send_json({"status": "invalid_sender"}, status=400)
                return
            rec_id = time.time_ns() // 1000
            msg = f"Вам отправлен импульс {symbol}"

            with DB_LOCK:
                RECENT_REACTIONS.append({
                    "id": rec_id,
                    "targetId": target_id,
                    "senderId": sender_id,
                    "symbol": symbol,
                    "message": msg,
                    "time": time.time()
                })
                if len(RECENT_REACTIONS) > 30:
                    del RECENT_REACTIONS[:-30]

                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("SELECT reactions_json FROM devices WHERE id = ?", (target_id,))
                row = cur.fetchone()
                if row:
                    try:
                        r_map = json.loads(row[0] or "{}")
                    except Exception:
                        r_map = {}
                    r_map[symbol] = int(r_map.get(symbol, 0)) + 1
                    cur.execute(
                        "UPDATE devices SET reactions_json = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(r_map, ensure_ascii=False), time.time(), target_id)
                    )
                    conn.commit()
                conn.close()

            print(f"[{datetime.now().strftime('%H:%M:%S')}] ✨ ОТКЛИК {symbol} от {sender_id} -> {target_id}")
            self._send_json(build_state_payload(for_device_id=sender_id))

        elif path == "/api/drop_capsule":
            cap_id = f"cap_{int(time.time() * 1000)}"
            creator_id = str(data.get("creatorId") or "local_user")
            artist = str(data.get("artist") or "Sirotkin")
            title = str(data.get("title") or "Август")
            zone_name = str(data.get("zoneName") or "Вечерняя улица")
            secret_msg = str(data.get("secretMessage") or "Слушай этот момент здесь...")
            is_venue = 1 if data.get("isVenue") else 0
            venue_name = str(data.get("venueName") or "").strip()

            curated = generate_curated_persona(artist, title, creator_id, use_ai=False)
            mood_label = str(data.get("moodLabel") or curated["moodLabel"])
            persona_title = venue_name if is_venue and venue_name else str(data.get("personaTitle") or curated["personaTitle"])

            angle_deg = float(random.randint(15, 345))
            dist_meters = random.randint(25, 140)
            dist_ratio = round(min(0.85, max(0.24, dist_meters / 180.0)), 2)

            lat, lon = get_zone_jittered_coords(zone_name, cap_id)

            with DB_LOCK:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO capsules (
                        id, creator_id, artist, title, mood_label, persona_title,
                        zone_name, secret_message, distance_meters, angle_degrees,
                        distance_ratio, compatibility_percent, is_unlocked, lat, lon,
                        is_venue, venue_name, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """, (
                    cap_id, creator_id, artist, title, mood_label, persona_title,
                    zone_name, secret_msg, dist_meters, angle_deg,
                    dist_ratio, curated["compatibilityPercent"], lat, lon,
                    is_venue, venue_name, time.time()
                ))
                conn.commit()
                conn.close()

            log_tag = f"🏛️ ТАЙНИК ЛОКАЦИИ [{venue_name}]" if is_venue else "⏳ ТАЙНИК"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {log_tag} [{zone_name}] {artist} — {title}: «{secret_msg}»")
            self._send_json(build_state_payload(for_device_id=creator_id))

        elif path == "/api/bump_encounter":
            target_id = str(data.get("targetId") or "")
            sender_id = str(data.get("senderId") or "")
            with DB_LOCK:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE devices SET encounters_count = encounters_count + 1 WHERE id = ?",
                    (target_id,)
                )
                conn.commit()
                conn.close()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🤝 ПЕРЕСЕЧЕНИЕ с {target_id} зафиксировано в SQLite")
            self._send_json(build_state_payload(for_device_id=sender_id))

        elif path == "/api/delete_signal":
            target_id = safe_device_id(data.get("targetId") or data.get("id") or "")
            claimant = str(data.get("deviceId") or "").strip()
            if claimant and safe_device_id(claimant) != target_id:
                self._send_json({"status": "forbidden"}, status=403)
                return
            with DB_LOCK:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("DELETE FROM devices WHERE id = ?", (target_id,))
                # «Невидимка» = мгновенное отключение: история и встречи тоже уходят.
                cur.execute("DELETE FROM listening_history WHERE device_id = ?", (target_id,))
                try:
                    cur.execute("DELETE FROM encounters WHERE target_id = ?", (target_id,))
                except sqlite3.Error:
                    pass
                conn.commit()
                conn.close()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🗑️ СИГНАЛ {target_id} удален из эфира вместе с историей")
            self._send_json(build_state_payload())

        else:
            self._send_json(build_state_payload())


def start_udp_broadcast():
    def _broadcaster():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = f"ECHO_SERVER_DISCOVERY:{HTTP_PORT}".encode("utf-8")
        while True:
            try:
                sock.sendto(payload, ("255.255.255.255", UDP_DISCOVERY_PORT))
            except Exception:
                pass
            time.sleep(3.0)

    t = threading.Thread(target=_broadcaster, daemon=True)
    t.start()


def main():
    init_db()
    start_udp_broadcast()
    local_ip = get_local_ip()
    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), EchoRequestHandler)
    print("=" * 66)
    print(" 📡 ЭХО (Echo Radar) — Ночной акустический атлас запущен!")
    print(f" 🌐 Локальный адрес в сети : http://{local_ip}:{HTTP_PORT}")
    print(f" 📱 Сенсорный Сонар и Карта : http://{local_ip}:{HTTP_PORT}/app")
    print(f" ⚙️ Пульт управления и ИИ : http://{local_ip}:{HTTP_PORT}/")
    print(f" 📡 UDP-автопоиск           : порт {UDP_DISCOVERY_PORT} (ECHO_SERVER_DISCOVERY:{HTTP_PORT})")
    print(f" 💾 База данных SQLite      : {DB_PATH}")
    print("=" * 66)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер ЭХО остановлен.")
        server.server_close()


if __name__ == "__main__":
    main()