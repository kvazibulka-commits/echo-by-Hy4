package ru.echo.radar

import android.app.Notification
import android.content.ComponentName
import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Handler
import android.os.Looper
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

class EchoMediaListenerService : NotificationListenerService() {

    private var mediaSessionManager: MediaSessionManager? = null
    private val mainHandler = Handler(Looper.getMainLooper())
    private val registeredCallbacks = mutableMapOf<MediaController, MediaController.Callback>()

    /** Снимаем слушатели при переподключении: иначе после kill сервиса копятся утечки. */
    private fun clearCallbacks() {
        registeredCallbacks.forEach { (controller, callback) ->
            try {
                controller.unregisterCallback(callback)
            } catch (_: Exception) {
            }
        }
        registeredCallbacks.clear()
    }

    private val sessionsChangedListener = MediaSessionManager.OnActiveSessionsChangedListener { controllers ->
        inspectControllers(controllers)
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        try {
            mediaSessionManager = getSystemService(Context.MEDIA_SESSION_SERVICE) as MediaSessionManager
            val componentName = ComponentName(this, EchoMediaListenerService::class.java)
            val activeControllers = mediaSessionManager?.getActiveSessions(componentName)
            inspectControllers(activeControllers)
            mediaSessionManager?.addOnActiveSessionsChangedListener(sessionsChangedListener, componentName)
        } catch (_: SecurityException) {}
    }

    override fun onListenerDisconnected() {
        super.onListenerDisconnected()
        clearCallbacks()
        try {
            mediaSessionManager?.removeOnActiveSessionsChangedListener(sessionsChangedListener)
        } catch (_: Exception) {}
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        super.onNotificationPosted(sbn)
        if (sbn == null) return

        val extras = sbn.notification?.extras ?: return
        val template = extras.getString(Notification.EXTRA_TEMPLATE)
        if (template != null && template.contains("MediaStyle")) {
            val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
            val artist = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
            if (title.isNotBlank() || artist.isNotBlank()) {
                EchoStateRepository.updateDetectedMusic(
                    artist = artist,
                    title = title,
                    packageName = sbn.packageName
                )
            }
        }
    }

    private fun inspectControllers(controllers: List<MediaController>?) {
        clearCallbacks()
        if (controllers.isNullOrEmpty()) return
        for (controller in controllers) {
            // Смена трека внутри того же плеера не меняет список активных сессий,
            // поэтому слушаем метаданные каждой сессии отдельно.
            val metadataCallback = object : MediaController.Callback() {
                override fun onMetadataChanged(metadata: MediaMetadata?) {
                    val artist = metadata?.getString(MediaMetadata.METADATA_KEY_ARTIST)
                        ?: metadata?.getString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST)
                        ?: ""
                    val title = metadata?.getString(MediaMetadata.METADATA_KEY_TITLE) ?: ""
                    if (title.isNotBlank()) {
                        EchoStateRepository.updateDetectedMusic(artist, title, controller.packageName)
                    }
                }
            }
            try {
                controller.registerCallback(metadataCallback, mainHandler)
                registeredCallbacks[controller] = metadataCallback
            } catch (_: SecurityException) {
            }
            val state = controller.playbackState?.state
            val metadata = controller.metadata
            if (metadata != null && (state == PlaybackState.STATE_PLAYING || state == PlaybackState.STATE_BUFFERING)) {
                val artist = metadata.getString(MediaMetadata.METADATA_KEY_ARTIST)
                    ?: metadata.getString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST)
                    ?: ""
                val title = metadata.getString(MediaMetadata.METADATA_KEY_TITLE) ?: ""
                if (title.isNotBlank()) {
                    EchoStateRepository.updateDetectedMusic(
                        artist = artist,
                        title = title,
                        packageName = controller.packageName
                    )
                    break
                }
            }
        }
    }
}
