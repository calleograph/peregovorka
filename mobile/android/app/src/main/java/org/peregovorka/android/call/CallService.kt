package org.peregovorka.android.call

import android.Manifest
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import org.peregovorka.android.App
import org.peregovorka.android.MainActivity
import org.peregovorka.android.R

/**
 * Служба переднего плана на время конференции: без неё Android прекращает запись микрофона у свёрнутого приложения.
 * Тип службы — только microphone: фоновую камеру и захват экрана приложение не обещает (система их ограничивает).
 */
class CallService : Service() {
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val calls = App.instance.calls
        when (intent?.action) {
            ACTION_LEAVE -> { calls.leave(); stopSelf(); return START_NOT_STICKY }
            ACTION_MIC -> calls.toggleMic()
            ACTION_STOP -> { stopSelf(); return START_NOT_STICKY }
        }
        if (!calls.active) { stopSelf(); return START_NOT_STICKY }
        val n = notification(intent?.getStringExtra(EXTRA_ROOM) ?: calls.ui.value.roomName, intent?.getStringExtra(EXTRA_SERVER) ?: calls.ui.value.serverTitle)
        // Тип microphone допустим, только если микрофон разрешён (Android 14 иначе завершает приложение); слушатель без микрофона получает тип «воспроизведение».
        val mic = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        ServiceCompat.startForeground(this, ID, n, if (mic) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        if (wake == null) {
            wake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "peregovorka:call").apply { setReferenceCounted(false); acquire(MAX_CALL_MS) }
        }
        return START_NOT_STICKY
    }

    private fun action(name: String, title: String, icon: Int) = NotificationCompat.Action.Builder(
        icon, title, PendingIntent.getService(this, name.hashCode(), Intent(this, CallService::class.java).setAction(name), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT),
    ).build()

    private fun notification(room: String, server: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, App.CHANNEL_CALL)
            .setSmallIcon(R.drawable.ic_call).setContentTitle("Идёт конференция").setContentText(if (server.isBlank()) room else "$room · $server")
            .setContentIntent(open).setOngoing(true).setCategory(NotificationCompat.CATEGORY_CALL).setOnlyAlertOnce(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(action(ACTION_MIC, "Микрофон", R.drawable.ic_call))
            .addAction(action(ACTION_LEAVE, "Выйти", R.drawable.ic_call))
            .build()
    }

    override fun onDestroy() { wake?.takeIf { it.isHeld }?.release(); wake = null; super.onDestroy() }

    companion object {
        private const val ID = 1001
        private const val ACTION_LEAVE = "org.peregovorka.android.LEAVE"
        private const val ACTION_MIC = "org.peregovorka.android.MIC"
        private const val ACTION_STOP = "org.peregovorka.android.STOP"
        private const val EXTRA_ROOM = "room"
        private const val EXTRA_SERVER = "server"
        private const val MAX_CALL_MS = 12L * 60 * 60 * 1000       // страховка: блокировка процессора не может висеть дольше 12 часов

        fun start(ctx: Context, room: String, server: String) =
            ContextCompat.startForegroundService(ctx, Intent(ctx, CallService::class.java).putExtra(EXTRA_ROOM, room).putExtra(EXTRA_SERVER, server))
        fun stop(ctx: Context) { ctx.startService(Intent(ctx, CallService::class.java).setAction(ACTION_STOP)) }
    }
}
