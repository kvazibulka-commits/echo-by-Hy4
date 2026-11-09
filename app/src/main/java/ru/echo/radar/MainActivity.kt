package ru.echo.radar

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import kotlinx.coroutines.delay
import kotlin.math.cos
import kotlin.math.sin

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Персистентные настройки (deviceId, адрес сервера) — до первого кадра UI
        EchoStateRepository.init(applicationContext)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    background = Color(0xFF06080D),
                    surface = Color(0xFF0D121E),
                    primary = Color(0xFF818CF8),
                    secondary = Color(0xFF38BDF8)
                )
            ) {
                EchoRadarAppScreen()
            }
        }
    }
}

@Composable
fun EchoRadarAppScreen() {
    val context = LocalContext.current
    val currentTrack by EchoStateRepository.currentTrack.collectAsState()
    val presenceActive by EchoStateRepository.presenceActive.collectAsState()
    var selectedSignal by remember { mutableStateOf<NearbyEchoSignal?>(null) }
    var sentSymbolMessage by remember { mutableStateOf("") }
    var showManualDialog by remember { mutableStateOf(false) }
    var showServerDialog by remember { mutableStateOf(false) }
    var showConsentDialog by remember { mutableStateOf(false) }
    var serverHostInput by remember { mutableStateOf(EchoStateRepository.serverBaseUrl.value) }
    var apiTokenInput by remember { mutableStateOf("") }
    val consentShown by EchoStateRepository.consentShown.collectAsState()
    val connectionStatus by EchoStateRepository.connectionStatus.collectAsState()
    val isOnline by EchoStateRepository.isOnline.collectAsState()
    val serverBaseUrl by EchoStateRepository.serverBaseUrl.collectAsState()
    val discoveryStatus by EchoStateRepository.discoveryStatus.collectAsState()
    val nearbySignalsFlow by EchoStateRepository.nearbySignals.collectAsState()
    val incomingEcho by EchoStateRepository.incomingEcho.collectAsState()
    val myColorHex by EchoStateRepository.myColorHex.collectAsState()
    val myPetals by EchoStateRepository.myPetals.collectAsState()

    // Опрос и автопоиск живут только пока экран на переднем плане
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> {
                    EchoStateRepository.startPolling()
                    EchoStateRepository.startDiscovery()
                }
                Lifecycle.Event.ON_PAUSE -> {
                    EchoStateRepository.stopPolling()
                    EchoStateRepository.stopDiscovery()
                }
                else -> { }
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    // Входящий импульс показываем ~10 секунд
    var echoVisible by remember { mutableStateOf(false) }
    LaunchedEffect(incomingEcho) {
        if (incomingEcho != null) {
            echoVisible = true
            delay(10000)
            echoVisible = false
        }
    }

    // Consent-first: до явного выбора присутствие выключено.
    LaunchedEffect(consentShown) {
        if (!consentShown) showConsentDialog = true
    }

    val infiniteTransition = rememberInfiniteTransition(label = "echo_pulse")
    val wavePhase by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 6.283f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 7000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "wave"
    )

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF06080D))
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "Э Х О",
                    color = Color(0xFFF1F5F9),
                    fontSize = 20.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 6.sp
                )
                Text(
                    text = "Музыкальное присутствие вокруг тебя",
                    color = Color(0xFF94A3B8),
                    fontSize = 11.sp
                )
            }

            Button(
                onClick = { EchoStateRepository.togglePresence() },
                modifier = Modifier.semantics {
                    contentDescription = if (presenceActive) "Присутствие включено, нажмите чтобы стать невидимкой"
                    else "Присутствие выключено, нажмите чтобы открыться для музыки"
                },
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (presenceActive) Color(0xFF0C2D48) else Color(0xFF1E293B)
                ),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
            ) {
                Text(
                    text = if (presenceActive) "● Присутствие: Вкл" else "○ Невидимка",
                    color = if (presenceActive) Color(0xFF7DD3FC) else Color(0xFF94A3B8),
                    fontSize = 11.sp
                )
            }
        }

        // Статус подключения / автопоиска сервера
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            val statusText = when {
                serverBaseUrl.isNotBlank() -> connectionStatus
                discoveryStatus.isNotBlank() -> discoveryStatus
                else -> "Сервер не задан — ждём маяк или введите адрес (⚙)"
            }
            Text(
                text = statusText,
                color = when {
                    isOnline -> Color(0xFF34D399)
                    serverBaseUrl.isBlank() -> Color(0xFFFBBF24)
                    else -> Color(0xFF94A3B8)
                },
                fontSize = 11.sp,
                modifier = Modifier.weight(1f)
            )
            TextButton(onClick = { showServerDialog = true }) {
                Text("⚙ Сервер", color = Color(0xFF818CF8), fontSize = 11.sp)
            }
        }

        // Входящий импульс (таргетирован именно этому устройству)
        val echo = incomingEcho
        if (echoVisible && echo != null) {
            Text(
                text = "${echo.symbol} ${echo.message}",
                color = Color(0xFFC7D2FE),
                fontSize = 12.sp,
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(10.dp))
                    .background(Color(0xFF1E1B4B))
                    .padding(horizontal = 10.dp, vertical = 8.dp)
            )
        }

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(20.dp))
                .background(Color(0xFF0D1322))
                .border(1.dp, Color(0xFF1E293B), RoundedCornerShape(20.dp))
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                GenerativeAvatarCanvas(
                    phase = wavePhase,
                    petals = myPetals,
                    colorHex = myColorHex,
                    modifier = Modifier.size(46.dp)
                )
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "В ВАШИХ НАУШНИКАХ · ${currentTrack.sourceApp}",
                        color = Color(0xFF818CF8),
                        fontSize = 10.sp
                    )
                    Text(
                        text = "♫ ${currentTrack.artist} — ${currentTrack.title}",
                        color = Color.White,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        text = "Ваш образ ИИ: ${currentTrack.poeticPersona}",
                        color = Color(0xFF94A3B8),
                        fontSize = 11.sp
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                OutlinedButton(
                    onClick = {
                        context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                    },
                    modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 6.dp)
                ) {
                    Text("Доступ к плеерам ОС", fontSize = 11.sp, color = Color(0xFFCBD5E1))
                }

                OutlinedButton(
                    onClick = { showManualDialog = true },
                    modifier = Modifier.weight(1f),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 6.dp)
                ) {
                    Text("Ввести трек вручную", fontSize = 11.sp, color = Color(0xFF818CF8))
                }
            }
        }

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(290.dp)
                .semantics {
                    contentDescription = "Музыкальный радар, ${nearbySignalsFlow.size} сигналов вокруг"
                }
                .clip(RoundedCornerShape(24.dp))
                .background(Color(0xFF05070C))
                .border(1.dp, Color(0xFF1E293B), RoundedCornerShape(24.dp)),
            contentAlignment = Alignment.Center
        ) {
            Canvas(modifier = Modifier.fillMaxSize()) {
                val center = Offset(size.width / 2f, size.height / 2f)
                val maxR = size.minDimension * 0.44f

                listOf(0.38f, 0.68f, 0.95f).forEach { factor ->
                    drawCircle(
                        color = Color(0xFF1E293B),
                        radius = (maxR * factor).coerceAtLeast(1f),
                        center = center,
                        style = Stroke(width = 1.5f)
                    )
                }

                // Блипы сигналов: угол, дистанция и цвет приходят с сервера
                nearbySignalsFlow.forEach { sig ->
                    val rad = Math.toRadians(sig.angleDeg.toDouble())
                    val dist = sig.ringFactor.coerceIn(0.06f, 1f) * maxR * 0.92f
                    val bx = center.x + (dist * cos(rad)).toFloat()
                    val by = center.y + (dist * sin(rad)).toFloat()
                    val blip = Color(sig.colorHex)
                    drawCircle(
                        color = blip.copy(alpha = if (sig.isGhost) 0.35f else 0.85f),
                        radius = (if (sig.isGhost) 9.dp else 13.dp).toPx(),
                        center = Offset(bx, by)
                    )
                    drawCircle(
                        color = Color.White.copy(alpha = if (sig.isGhost) 0.5f else 1f),
                        radius = (if (sig.isGhost) 2.5.dp else 4.dp).toPx(),
                        center = Offset(bx, by)
                    )
                }

                // Центр — это вы
                drawCircle(
                    color = Color(myColorHex),
                    radius = 7.dp.toPx(),
                    center = center
                )
            }

            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(12.dp),
                verticalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "Нажмите на звучащую песню, чтобы открыть слушателя:",
                    color = Color(0xFF64748B),
                    fontSize = 11.sp
                )

                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    nearbySignalsFlow.forEach { sig ->
                        val isSelected = sig.id == selectedSignal?.id
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(14.dp))
                                .background(
                                    if (isSelected) Color(0xFF1E1B4B) else Color(0xCC0F172A)
                                )
                                .border(
                                    1.dp,
                                    if (isSelected) Color(0xFF818CF8) else Color(0xFF1E293B),
                                    RoundedCornerShape(14.dp)
                                )
                                .clickable {
                                    selectedSignal = sig
                                    sentSymbolMessage = ""
                                }
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "♫ ${sig.artist} — ${sig.title}",
                                color = Color.White,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Medium,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f)
                            )
                            Text(
                                text = sig.distanceLabel,
                                color = Color(0xFF94A3B8),
                                fontSize = 10.sp
                            )
                        }
                    }
                }
            }
        }

        selectedSignal?.let { sig ->
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(24.dp))
                .background(Color(0xFF0D121E))
                .border(1.dp, Color(0xFF1E293B), RoundedCornerShape(24.dp))
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                GenerativeAvatarCanvas(
                    phase = wavePhase,
                    petals = sig.avatarPetals,
                    colorHex = sig.colorHex,
                    modifier = Modifier.size(54.dp)
                )
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "МУЗЫКАЛЬНОЕ ИМЯ (ИИ-КУРАТОР)",
                        color = Color(0xFF64748B),
                        fontSize = 10.sp
                    )
                    Text(
                        text = sig.poeticName,
                        color = Color.White,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                    Text(
                        text = "${sig.moodLabel} · ${sig.distanceLabel} · ${sig.sourceApp}",
                        color = Color(0xFF38BDF8),
                        fontSize = 11.sp
                    )
                }
            }

            if (sig.resonanceDays != null) {
                Text(
                    text = "✦ ${sig.resonanceDays}",
                    color = Color(0xFFC7D2FE),
                    fontSize = 11.sp,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(Color(0xFF1E1B4B))
                        .padding(8.dp)
                )
            }

            Text(
                text = "«${sig.aiObservation}»",
                color = Color(0xFFCBD5E1),
                fontSize = 12.sp,
                fontStyle = FontStyle.Italic
            )

            Text(
                text = "Отправить музыкальное настроение (без лайков и оценок):",
                color = Color(0xFF94A3B8),
                fontSize = 11.sp
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                listOf("🦇", "🌑", "🌊", "⚔", "🌲", "🎼", "☁").forEach { symbol ->
                    Box(
                        modifier = Modifier
                            .size(38.dp)
                            .clip(CircleShape)
                            .background(Color(0xFF161F33))
                            .clickable {
                                EchoStateRepository.reactToSignal(sig.id, symbol) { ok ->
                                    sentSymbolMessage = if (ok) {
                                        "Тихий знак $symbol отправлен для «${sig.poeticName}»"
                                    } else {
                                        "Не удалось отправить — нет связи с сервером"
                                    }
                                }
                            },
                        contentAlignment = Alignment.Center
                    ) {
                        Text(text = symbol, fontSize = 16.sp)
                    }
                }
            }

            if (sentSymbolMessage.isNotEmpty()) {
                Text(
                    text = sentSymbolMessage,
                    color = Color(0xFF818CF8),
                    fontSize = 11.sp
                )
            }
        }
        } // end selectedSignal?.let
    }

    // Онбординг: явное согласие на музыкальное присутствие (opt-in по Конституции).
    if (showConsentDialog) {
        AlertDialog(
            onDismissRequest = { /* выбор обязателен */ },
            title = { Text("Музыкальное присутствие", color = Color.White) },
            text = {
                Text(
                    "Сейчас ЭХО никому не показывает вашу музыку — вы невидимка.\n\n" +
                        "Если открыться, рядом увидят только трек, который играет, и поэтичный образ. " +
                        "Ни фото, ни координат, ни истории. В любой момент можно стать невидимкой одной кнопкой.",
                    color = Color(0xFFCBD5E1),
                    fontSize = 13.sp
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    EchoStateRepository.markConsentSeen()
                    EchoStateRepository.enablePresence()
                    showConsentDialog = false
                }) { Text("Открыться для музыки", color = Color(0xFF38BDF8)) }
            },
            dismissButton = {
                TextButton(onClick = {
                    EchoStateRepository.markConsentSeen()
                    showConsentDialog = false
                }) { Text("Остаться невидимкой", color = Color(0xFF94A3B8)) }
            },
            containerColor = Color(0xFF0D1322)
        )
    }

    if (showManualDialog) {
        ManualTrackDialog(
            onDismiss = { showManualDialog = false },
            onSubmit = { artist, title ->
                EchoStateRepository.updateDetectedMusic(artist, title, "Ручной режим")
                showManualDialog = false
            }
        )
    }

    // Диалог настройки сервера
    if (showServerDialog) {
        AlertDialog(
            onDismissRequest = { showServerDialog = false },
            title = { Text("Адрес сервера ЭХО", color = Color.White) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "В одной Wi-Fi сети сервер найдётся сам.\nЛибо введите вручную:\nhttp://192.168.x.x:8080 или abc123.trycloudflare.com",
                        color = Color(0xFF94A3B8),
                        fontSize = 12.sp
                    )
                    OutlinedTextField(
                        value = serverHostInput,
                        onValueChange = { serverHostInput = it },
                        placeholder = { Text("192.168.1.5:8080", fontSize = 12.sp) },
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = Color.White,
                            unfocusedTextColor = Color(0xFFCBD5E1)
                        )
                    )
                    Text(
                        "Токен туннеля — только если сервер запущен с ECHO_API_TOKEN",
                        color = Color(0xFF64748B),
                        fontSize = 10.sp
                    )
                    OutlinedTextField(
                        value = apiTokenInput,
                        onValueChange = { apiTokenInput = it },
                        placeholder = { Text("необязательно", fontSize = 12.sp) },
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = Color.White,
                            unfocusedTextColor = Color(0xFFCBD5E1)
                        )
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    EchoStateRepository.setApiToken(apiTokenInput)
                    EchoStateRepository.setServerHost(serverHostInput)
                    showServerDialog = false
                }) {
                    Text("Подключить", color = Color(0xFF38BDF8))
                }
            },
            dismissButton = {
                TextButton(onClick = { showServerDialog = false }) {
                    Text("Отмена", color = Color(0xFF94A3B8))
                }
            },
            containerColor = Color(0xFF0D1322)
        )
    }
}

@Composable
fun GenerativeAvatarCanvas(phase: Float, petals: Int, colorHex: Long, modifier: Modifier = Modifier) {
    Canvas(
        modifier = modifier
            .clip(CircleShape)
            .background(Color(0xFF05070C))
            .border(1.dp, Color(0xFF334155), CircleShape)
    ) {
        val cx = size.width / 2f
        val cy = size.height / 2f
        val baseR = (size.minDimension * 0.28f).coerceAtLeast(2f)
        val petalCount = petals.coerceIn(4, 9)
        val baseColor = Color(colorHex)

        val path = Path()
        val steps = 48
        for (i in 0..steps) {
            val theta = (i.toFloat() / steps) * 6.28318f
            val wave = sin(theta * petalCount + phase) * (baseR * 0.25f)
            val r = (baseR + wave).coerceAtLeast(1f)
            val x = cx + cos(theta) * r
            val y = cy + sin(theta) * r
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        path.close()

        drawPath(
            path = path,
            brush = Brush.radialGradient(
                colors = listOf(
                    baseColor.copy(alpha = 0.95f),
                    baseColor.copy(alpha = 0.40f),
                    Color.Transparent
                )
            )
        )
        drawPath(
            path = path,
            color = baseColor.copy(alpha = 0.95f),
            style = Stroke(width = 2f)
        )
    }
}

@Composable
fun ManualTrackDialog(onDismiss: () -> Unit, onSubmit: (String, String) -> Unit) {
    var artist by remember { mutableStateOf("Bohren & der Club of Gore") }
    var title by remember { mutableStateOf("Prowler") }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Color(0xFF0F172A),
        title = {
            Text("Поделиться треком вручную", color = Color.White, fontSize = 16.sp)
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = artist,
                    onValueChange = { artist = it },
                    label = { Text("Исполнитель") },
                    singleLine = true
                )
                OutlinedTextField(
                    value = title,
                    onValueChange = { title = it },
                    label = { Text("Название трека") },
                    singleLine = true
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onSubmit(artist, title) }) {
                Text("Транслировать в ЭХО", color = Color(0xFF818CF8))
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Отмена", color = Color(0xFF94A3B8))
            }
        }
    )
}
