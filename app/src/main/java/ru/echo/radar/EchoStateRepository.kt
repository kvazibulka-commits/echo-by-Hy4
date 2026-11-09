package ru.echo.radar

import android.content.Context
import android.util.Log
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.SocketTimeoutException
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.TimeUnit

// DataStore настроек ЭХО: deviceId, адрес сервера, режим присутствия
private val Context.echoDataStore by preferencesDataStore(name = "echo_settings")

data class PlayingTrack(
    val artist: String,
    val title: String,
    val sourceApp: String,
    val poeticPersona: String,
    val mood: String
)

data class NearbyEchoSignal(
    val id: String,
    val artist: String,
    val title: String,
    val sourceApp: String,
    val distanceLabel: String,
    val distanceBucket: String = "medium",
    val poeticName: String,
    val aiObservation: String,
    val moodLabel: String,
    val compatibility: Int,
    val resonanceDays: String?,
    val angleDeg: Float,
    val ringFactor: Float,
    val colorHex: Long = 0xFF38BDF8L,
    val avatarPetals: Int = 6,
    val isGhost: Boolean = false
)

/** Входящий импульс — сервер отправляет его только устройству-адресату. */
data class IncomingEcho(
    val id: Long,
    val senderId: String,
    val symbol: String,
    val message: String
)

object EchoStateRepository {

    private const val TAG = "EchoRepo"

    private val KEY_DEVICE_ID = stringPreferencesKey("device_id")
    private val KEY_SERVER_HOST = stringPreferencesKey("server_host")
    private val KEY_PRESENCE = booleanPreferencesKey("presence")
    private val KEY_API_TOKEN = stringPreferencesKey("api_token")
    private val KEY_CONSENT_SHOWN = booleanPreferencesKey("consent_shown")

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .writeTimeout(5, TimeUnit.SECONDS)
        .build()

    @Volatile
    private var apiToken: String = ""

    /** Токен публичного туннеля передаётся только заголовком, никогда не попадает в HTML. */
    private fun Request.Builder.withEchoAuth(): Request.Builder = apply {
        if (apiToken.isNotEmpty()) header("X-API-Key", apiToken)
    }

    fun setApiToken(raw: String) {
        apiToken = raw.trim()
        val ctx = appContext ?: return
        scope.launch { ctx.echoDataStore.edit { it[KEY_API_TOKEN] = apiToken } }
    }

    private var appContext: Context? = null

    // ── Адрес сервера (полная база, например http://192.168.1.5:8080) ──
    private val _serverBaseUrl = MutableStateFlow("")
    val serverBaseUrl: StateFlow<String> = _serverBaseUrl.asStateFlow()

    // ── Автопоиск сервера в Wi-Fi сети (UDP-маяк) ──
    private val _discoveryStatus = MutableStateFlow("")
    val discoveryStatus: StateFlow<String> = _discoveryStatus.asStateFlow()

    // ── Текущий трек ──
    private val _currentTrack = MutableStateFlow(
        PlayingTrack(
            artist = "Ожидание музыки...",
            title = "Включите трек в Яндекс Музыке / VK / Плеере",
            sourceApp = "Системный перехват Android",
            poeticPersona = "Архивариус ночных шорохов",
            mood = "Ночная тишина"
        )
    )
    val currentTrack: StateFlow<PlayingTrack> = _currentTrack.asStateFlow()

    // ── Присутствие: строго opt-in, по умолчанию выключено ──
    private val _presenceActive = MutableStateFlow(false)
    val presenceActive: StateFlow<Boolean> = _presenceActive.asStateFlow()

    // ── Пользователь уже увидел объяснение присутствия ──
    private val _consentShown = MutableStateFlow(false)
    val consentShown: StateFlow<Boolean> = _consentShown.asStateFlow()

    fun markConsentSeen() {
        _consentShown.value = true
        val ctx = appContext ?: return
        scope.launch { ctx.echoDataStore.edit { it[KEY_CONSENT_SHOWN] = true } }
    }

    // ── Мой образ с сервера (цвет и лепестки аватара) ──
    private val _myColorHex = MutableStateFlow(0xFF38BDF8L)
    val myColorHex: StateFlow<Long> = _myColorHex.asStateFlow()

    private val _myPetals = MutableStateFlow(6)
    val myPetals: StateFlow<Int> = _myPetals.asStateFlow()

    // ── Сигналы с сервера ──
    private val _nearbySignals = MutableStateFlow<List<NearbyEchoSignal>>(emptyList())
    val nearbySignals: StateFlow<List<NearbyEchoSignal>> = _nearbySignals.asStateFlow()

    // ── Последний входящий импульс ──
    private val _incomingEcho = MutableStateFlow<IncomingEcho?>(null)
    val incomingEcho: StateFlow<IncomingEcho?> = _incomingEcho.asStateFlow()

    // ── Статус подключения ──
    private val _connectionStatus = MutableStateFlow("Не подключено")
    val connectionStatus: StateFlow<String> = _connectionStatus.asStateFlow()

    private val _isOnline = MutableStateFlow(false)
    val isOnline: StateFlow<Boolean> = _isOnline.asStateFlow()

    private var deviceId: String = ""

    private var pollingJob: Job? = null
    private var discoveryJob: Job? = null
    private var lastSentTrack: Pair<String, String>? = null
    private var lastShownEchoId: Long = 0L

    /** Вызывается один раз из MainActivity.onCreate до первого кадра UI. */
    fun init(context: Context) {
        val ctx = context.applicationContext
        if (appContext === ctx) return
        appContext = ctx

        val prefs = runBlocking(Dispatchers.IO) { ctx.echoDataStore.data.first() }

        deviceId = prefs[KEY_DEVICE_ID] ?: run {
            val fresh = "echo_android_" + UUID.randomUUID().toString().replace("-", "").take(10)
            scope.launch { ctx.echoDataStore.edit { it[KEY_DEVICE_ID] = fresh } }
            fresh
        }
        _serverBaseUrl.value = prefs[KEY_SERVER_HOST] ?: ""
        apiToken = prefs[KEY_API_TOKEN] ?: ""
        _presenceActive.value = prefs[KEY_PRESENCE] ?: false
        _consentShown.value = prefs[KEY_CONSENT_SHOWN] ?: false
    }

    // ── Адрес сервера ──

    /**
     * Принимает «abc123.trycloudflare.com», «http://192.168.1.5:8080»,
     * «192.168.1.5:8080», «localhost:8080» — и приводит к полному адресу.
     * Локальные адреса получают http, всё неизвестное — https.
     */
    private fun normalizeBaseUrl(raw: String): String {
        var s = raw.trim().trimEnd('/')
        if (s.isBlank()) return ""
        val lowerAll = s.lowercase()
        val hadHttp = lowerAll.startsWith("http://")
        val hadHttps = lowerAll.startsWith("https://")
        s = if (hadHttp || hadHttps) s.substringAfter("://") else s
        s = s.trimEnd('/')
        if (s.isBlank()) return ""

        val host = s.lowercase()
        val isLocal = host.startsWith("localhost") ||
            host.startsWith("127.") ||
            host.startsWith("10.") ||
            host.startsWith("192.168.") ||
            host.startsWith("172.16.") || host.startsWith("172.17.") ||
            host.startsWith("172.18.") || host.startsWith("172.19.") ||
            host.startsWith("172.2") || host.startsWith("172.30.") ||
            host.startsWith("172.31.")
        val scheme = when {
            hadHttps -> "https"
            hadHttp -> "http"
            isLocal -> "http"
            else -> "https"
        }
        return "$scheme://$s"
    }

    fun setServerHost(rawInput: String) {
        val normalized = normalizeBaseUrl(rawInput)
        _serverBaseUrl.value = normalized
        stopDiscovery()

        val ctx = appContext
        if (ctx != null) {
            scope.launch { ctx.echoDataStore.edit { it[KEY_SERVER_HOST] = normalized } }
        }

        if (normalized.isNotBlank()) {
            startPolling()
        } else {
            stopPolling()
        }
    }

    // ── Автопоиск: сервер каждые 3 секунды вещает маяк в сеть (порт 8888) ──

    fun startDiscovery() {
        if (_serverBaseUrl.value.isNotBlank()) return
        if (discoveryJob?.isActive == true) return
        discoveryJob = scope.launch {
            _discoveryStatus.value = "Ищу сервер ЭХО в Wi-Fi сети…"
            val buf = ByteArray(256)
            while (isActive && _serverBaseUrl.value.isBlank()) {
                var socket: DatagramSocket? = null
                try {
                    socket = DatagramSocket(8888)
                    socket.broadcast = true
                    socket.soTimeout = 5000
                    val packet = DatagramPacket(buf, buf.size)
                    socket.receive(packet)
                    val text = String(packet.data, packet.offset, packet.length, Charsets.UTF_8)
                    if (text.startsWith("ECHO_SERVER_DISCOVERY:")) {
                        val port = text.substringAfter("ECHO_SERVER_DISCOVERY:").trim().toIntOrNull() ?: 8080
                        val ip = packet.address?.hostAddress ?: ""
                        if (ip.isNotBlank()) {
                            val found = "http://$ip:$port"
                            _discoveryStatus.value = "Сервер найден → $found"
                            setServerHost(found)
                        }
                    }
                } catch (_: SocketTimeoutException) {
                    // В сети тихо — продолжаем слушать маяк
                } catch (_: Exception) {
                    _discoveryStatus.value = "Автопоиск недоступен — введите адрес вручную (⚙)"
                    delay(4000)
                } finally {
                    try { socket?.close() } catch (_: Exception) { }
                }
            }
        }
    }

    fun stopDiscovery() {
        discoveryJob?.cancel()
        discoveryJob = null
        if (_serverBaseUrl.value.isBlank()) {
            _discoveryStatus.value = ""
        }
    }

    // ── Периодическая синхронизация ──

    fun startPolling() {
        if (_serverBaseUrl.value.isBlank()) return
        pollingJob?.cancel()
        pollingJob = scope.launch {
            while (isActive) {
                syncWithServer()
                delay(4000)
            }
        }
    }

    fun stopPolling() {
        pollingJob?.cancel()
        pollingJob = null
    }

    /** Явное согласие из онбординга: включает присутствие только один раз. */
    fun enablePresence() {
        if (_presenceActive.value) return
        togglePresence()
    }

    fun togglePresence() {
        val newValue = !_presenceActive.value
        _presenceActive.value = newValue

        val ctx = appContext
        if (ctx != null) {
            scope.launch { ctx.echoDataStore.edit { it[KEY_PRESENCE] = newValue } }
        }

        if (!newValue) {
            // Невидимка: удаляем себя из эфира
            scope.launch { deleteSignal() }
        } else {
            // Возвращаемся: переотправляем последний трек
            val sent = lastSentTrack
            if (sent != null) {
                scope.launch { sendSignalToServer(sent.first, sent.second, _currentTrack.value.sourceApp) }
            }
        }
    }

    fun updateDetectedMusic(artist: String, title: String, packageName: String) {
        val readableSource = when {
            packageName.contains("yandex", ignoreCase = true) -> "Яндекс Музыка"
            packageName.contains("vkontakte", ignoreCase = true) || packageName.contains("vk.music", ignoreCase = true) -> "VK Музыка"
            packageName.contains("zvuk", ignoreCase = true) || packageName.contains("sber", ignoreCase = true) -> "Звук"
            packageName.contains("spotify", ignoreCase = true) -> "Spotify"
            packageName.contains("telegram", ignoreCase = true) -> "Telegram Плеер"
            packageName.contains("aimp", ignoreCase = true) -> "AIMP"
            packageName.contains("poweramp", ignoreCase = true) -> "Poweramp"
            else -> "Плеер Android"
        }

        val newArtist = artist.ifBlank { "Неизвестный исполнитель" }
        val newTitle = title.ifBlank { "Без названия" }

        // Обновляем UI сразу с временной персоной
        _currentTrack.value = PlayingTrack(
            artist = newArtist,
            title = newTitle,
            sourceApp = readableSource,
            poeticPersona = "Куратор вслушивается…",
            mood = "Живое присутствие"
        )

        // Отправляем на сервер только если трек изменился
        if (lastSentTrack != Pair(newArtist, newTitle)) {
            lastSentTrack = Pair(newArtist, newTitle)
            if (_presenceActive.value) {
                scope.launch { sendSignalToServer(newArtist, newTitle, readableSource) }
            }
        }
    }

    /** Реакция «тихий знак»: реально уходит на сервер адресату сигнала. */
    fun reactToSignal(targetId: String, symbol: String, onDone: (Boolean) -> Unit = {}) {
        val base = _serverBaseUrl.value
        if (base.isBlank() || targetId.isBlank()) {
            onDone(false)
            return
        }
        scope.launch {
            var ok = false
            try {
                val body = JSONObject().apply {
                    put("targetId", targetId)
                    put("senderId", deviceId)
                    put("symbol", symbol)
                }.toString()
                val request = Request.Builder()
                    .url("$base/api/react")
                    .post(body.toRequestBody("application/json".toMediaType()))
                    .withEchoAuth()
                    .build()
                withContext(Dispatchers.IO) {
                    httpClient.newCall(request).execute().use { resp -> ok = resp.isSuccessful }
                }
            } catch (e: Exception) {
                Log.w(TAG, "React failed: ${e.message}")
            }
            withContext(Dispatchers.Main) { onDone(ok) }
        }
    }

    // ── Внутреннее ──

    private fun applyMyPersona(json: JSONObject) {
        val myPersona = json.optString("myCuratedPersona", "")
        val myMood = json.optString("myRefinedMood", "")
        if (myPersona.isNotBlank()) {
            val cur = _currentTrack.value
            _currentTrack.value = cur.copy(poeticPersona = myPersona, mood = myMood.ifBlank { cur.mood })
        }
        val myColor = json.optLong("myColorHex", 0L)
        if (myColor != 0L) _myColorHex.value = myColor
        val myPetalsFromServer = json.optInt("myAvatarPetals", -1)
        if (myPetalsFromServer > 0) _myPetals.value = myPetalsFromServer
    }

    private suspend fun syncWithServer() {
        val base = _serverBaseUrl.value
        if (base.isBlank()) return

        val url = "$base/api/state?deviceId=" + URLEncoder.encode(deviceId, "UTF-8")
        val request = Request.Builder().url(url).get().withEchoAuth().build()

        try {
            val response = withContext(Dispatchers.IO) {
                httpClient.newCall(request).execute()
            }
            response.use { resp ->
                if (!resp.isSuccessful) {
                    _isOnline.value = false
                    _connectionStatus.value = "✗ Сервер ответил ошибкой ${resp.code}"
                    return
                }
                val body = resp.body?.string() ?: return
                val json = JSONObject(body)

                val signals = mutableListOf<NearbyEchoSignal>()
                val signalsArray = json.optJSONArray("signals")
                if (signalsArray != null) {
                    for (i in 0 until signalsArray.length()) {
                        val s = signalsArray.getJSONObject(i)
                        val id = s.optString("id", "")
                        if (id == deviceId) continue // не показываем себя

                        // Сервер отдаёт только крупный диапазон, а не точные метры.
                        val distBucket = s.optString("distanceBucket", "").ifBlank {
                            when (s.optInt("distanceMeters", 100)) {
                                in 0..79 -> "near"
                                in 80..179 -> "medium"
                                else -> "far"
                            }
                        }
                        val distLabel = when (distBucket) {
                            "near" -> "рядом"
                            "medium" -> "неподалёку"
                            else -> "далеко"
                        }
                        val resDays = s.optInt("resonanceDays", 0)

                        signals.add(
                            NearbyEchoSignal(
                                id = id,
                                artist = s.optString("artist", ""),
                                title = s.optString("title", ""),
                                sourceApp = s.optString("sourceName", "ЭХО"),
                                distanceLabel = distLabel,
                                distanceBucket = distBucket,
                                poeticName = s.optString("personaTitle", "Архивариус забытых кассет"),
                                aiObservation = s.optString("personaObservation", ""),
                                moodLabel = s.optString("moodLabel", ""),
                                compatibility = s.optInt("compatibilityPercent", 88),
                                resonanceDays = if (resDays > 0) "Музыкальный резонанс · $resDays дн." else null,
                                angleDeg = s.optDouble("angleDegrees", 45.0).toFloat(),
                                ringFactor = s.optDouble("distanceRatio", 0.5).toFloat(),
                                colorHex = s.optLong("colorHex", 0xFF38BDF8L),
                                avatarPetals = s.optInt("avatarPetals", 6),
                                isGhost = s.optBoolean("isGhost", false)
                            )
                        )
                    }
                }
                _nearbySignals.value = signals
                _isOnline.value = true
                _connectionStatus.value = "● Онлайн · ${signals.size} сигналов"

                // Если прошлый трек не дошёл из-за отвала сети — повторяем при
                // первом же успешном контакте с сервером.
                if (_presenceActive.value && lastSentTrack == null) {
                    val pending = _currentTrack.value
                    if (pending.artist != "Ожидание музыки..." && pending.title.isNotBlank()) {
                        lastSentTrack = Pair(pending.artist, pending.title)
                        sendSignalToServer(pending.artist, pending.title, pending.sourceApp)
                    }
                }

                applyMyPersona(json)

                val banner = json.optJSONObject("incomingBanner")
                if (banner != null) {
                    val bannerId = banner.optLong("id", 0L)
                    if (bannerId != lastShownEchoId) {
                        lastShownEchoId = bannerId
                        _incomingEcho.value = IncomingEcho(
                            id = bannerId,
                            senderId = banner.optString("senderId", ""),
                            symbol = banner.optString("symbol", "✦"),
                            message = banner.optString("message", "Вам отправлен импульс")
                        )
                    }
                }
            }
        } catch (e: IOException) {
            _isOnline.value = false
            _connectionStatus.value = "✗ Нет связи с сервером"
            Log.w(TAG, "Sync failed: ${e.message}")
        } catch (e: Exception) {
            Log.w(TAG, "Parse error: ${e.message}")
        }
    }

    private suspend fun sendSignalToServer(artist: String, title: String, sourceName: String) {
        val base = _serverBaseUrl.value
        if (base.isBlank() || !_presenceActive.value) return

        val body = JSONObject().apply {
            put("id", deviceId)
            put("artist", artist)
            put("title", title)
            put("sourceName", sourceName)
        }.toString()

        val request = Request.Builder()
            .url("$base/api/signal")
            .post(body.toRequestBody("application/json".toMediaType()))
            .withEchoAuth()
            .build()

        var delivered = false
        try {
            withContext(Dispatchers.IO) {
                httpClient.newCall(request).execute()
            }.use { response ->
                if (response.isSuccessful) {
                    delivered = true
                    val respBody = response.body?.string() ?: return
                    applyMyPersona(JSONObject(respBody))
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Signal send failed: ${e.message}")
        }
        if (!delivered) {
            // Разрешаем повторную отправку при следующем успешном опросе.
            lastSentTrack = null
            _isOnline.value = false
        }
    }

    private suspend fun deleteSignal() {
        val base = _serverBaseUrl.value
        if (base.isBlank()) return

        val body = JSONObject().apply {
            put("targetId", deviceId)
            put("deviceId", deviceId)
        }.toString()
        val request = Request.Builder()
            .url("$base/api/delete_signal")
            .post(body.toRequestBody("application/json".toMediaType()))
            .withEchoAuth()
            .build()

        try {
            withContext(Dispatchers.IO) { httpClient.newCall(request).execute() }.close()
        } catch (e: Exception) {
            // Тишина: мы и так невидимы
        }
    }
}
