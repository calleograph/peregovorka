package org.peregovorka.android

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.peregovorka.android.ui.AppRoot
import org.peregovorka.android.ui.AppViewModel
import org.peregovorka.android.ui.PeregovorkaTheme

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()
    private var link by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        link = intent?.dataString
        val app = application as App
        setContent {
            PeregovorkaTheme {
                Surface(color = MaterialTheme.colorScheme.background) {
                    AppRoot(vm, app.calls, app.store, link, onLinkHandled = { link = null }, onMinimize = { moveTaskToBack(true) })
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        link = intent.dataString
    }

    // Камера в фоне недоступна системе: выключаем при сворачивании и возвращаем при возврате. Звук продолжает служба CallService.
    override fun onStop() { super.onStop(); (application as App).calls.onBackground() }
    override fun onStart() { super.onStart(); (application as App).calls.onForeground() }
}
