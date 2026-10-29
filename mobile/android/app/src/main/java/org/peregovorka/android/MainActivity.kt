package org.peregovorka.android

import android.content.Intent
import android.os.Bundle
import android.util.Log
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
                    AppRoot(vm, app.calls, app.store, link, onLinkHandled = { link = null })
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        link = intent.dataString
    }

    // Камера в фоне недоступна системе: выключаем при сворачивании и возвращаем при возврате. Звук продолжает служба CallService.
    override fun onStop() { super.onStop(); lifecycleNote("onStop"); (application as App).calls.onBackground() }
    override fun onStart() { super.onStart(); lifecycleNote("onStart"); (application as App).calls.onForeground() }
    override fun onDestroy() { lifecycleNote("onDestroy"); super.onDestroy() }

    /** Жизненный цикл в журнале устройства (тег PGLIFE, без личных данных): по нему видно, почему приложение ушло с переднего плана — завершение, смена конфигурации или обычное сворачивание. */
    private fun lifecycleNote(event: String) {
        Log.i("PGLIFE", "$event finishing=$isFinishing configChange=$isChangingConfigurations call=${(application as App).calls.ui.value.phase}")
    }
}
