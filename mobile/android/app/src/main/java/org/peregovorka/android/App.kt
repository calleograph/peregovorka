package org.peregovorka.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import org.peregovorka.android.call.CallManager
import org.peregovorka.android.data.Store

class App : Application() {
    lateinit var store: Store
    lateinit var calls: CallManager

    override fun onCreate() {
        super.onCreate()
        instance = this
        store = Store(this)
        calls = CallManager(this)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL_CALL, "Идущая конференция", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Показывается, пока вы участвуете в конференции: звук продолжается при свёрнутом приложении."
                setShowBadge(false)
            },
        )
    }

    companion object {
        const val CHANNEL_CALL = "call"
        lateinit var instance: App
            private set
    }
}
