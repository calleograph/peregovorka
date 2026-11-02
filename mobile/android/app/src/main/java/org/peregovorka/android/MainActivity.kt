package org.peregovorka.android

import android.app.PictureInPictureParams
import android.app.RemoteAction
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.util.Rational
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import org.peregovorka.android.call.CallService
import org.peregovorka.android.call.CallUi
import org.peregovorka.android.call.Phase
import org.peregovorka.android.call.PipModel
import org.peregovorka.android.ui.AppRoot
import org.peregovorka.android.ui.AppViewModel
import org.peregovorka.android.ui.LocalInPip
import org.peregovorka.android.ui.PeregovorkaTheme

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()
    private var link by mutableStateOf<String?>(null)
    private var inPip by mutableStateOf(false)
    private val calls get() = (application as App).calls
    // На части устройств (Android Go, некоторые планшеты и приставки) системного плавающего окна нет — тогда при сворачивании всё как раньше: звук в фоне, возврат по уведомлению.
    private val pipSupported by lazy { packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        link = intent?.dataString
        val app = application as App
        setContent {
            PeregovorkaTheme {
                CompositionLocalProvider(LocalInPip provides inPip) {
                    Surface(color = MaterialTheme.colorScheme.background) {
                        AppRoot(vm, app.calls, app.store, link, onLinkHandled = { link = null })
                    }
                }
            }
        }
        // Параметры плавающего окна следуют за звонком: автопереход только во время встречи, кнопка микрофона показывает его состояние, форма — по демонстрации экрана.
        lifecycleScope.launch {
            calls.ui.map { Triple(PipModel.wanted(it.phase), it.micOn to it.canMic, shareAspect(it)) }.distinctUntilChanged().collect { updatePip() }
        }
        // Встреча закончилась, пока приложение было в плавающем окне: окно показывает «Встреча завершена» и через пару секунд убирается.
        lifecycleScope.launch {
            calls.ui.map { it.phase == Phase.Ended }.distinctUntilChanged().collect { ended -> if (ended && inPip) { delay(2_500); if (inPip) moveTaskToBack(false) } }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        link = intent.dataString
    }

    private fun shareAspect(s: CallUi): Pair<Int, Int> = s.tiles.firstOrNull { !it.isLocal && it.screen != null }.let { PipModel.aspect(it?.screenW ?: 0, it?.screenH ?: 0) }

    private fun pipParams(s: CallUi): PictureInPictureParams {
        val (w, h) = shareAspect(s)
        val mic = RemoteAction(
            Icon.createWithResource(this, if (s.micOn) R.drawable.ic_call else R.drawable.ic_mic_off),
            if (s.micOn) "Выключить микрофон" else "Включить микрофон", if (s.micOn) "Выключить микрофон" else "Включить микрофон",
            CallService.actionIntent(this, CallService.ACTION_MIC),
        ).apply { isEnabled = s.canMic }
        val leave = RemoteAction(Icon.createWithResource(this, R.drawable.ic_call_end), "Выйти", "Выйти из конференции", CallService.actionIntent(this, CallService.ACTION_LEAVE))
        val b = PictureInPictureParams.Builder().setAspectRatio(Rational(w, h)).setActions(listOf(mic, leave))
        if (Build.VERSION.SDK_INT >= 31) b.setAutoEnterEnabled(PipModel.wanted(s.phase))      // Android 12+: система сама переводит в окно при жесте «Домой» и переключении приложений
        if (Build.VERSION.SDK_INT >= 33) b.setTitle(s.roomName)
        return b.build()
    }

    private fun updatePip() {
        if (!pipSupported) return
        runCatching { setPictureInPictureParams(pipParams(calls.ui.value)) }.onFailure { Log.w("PGLIFE", "pip_params ${it.javaClass.simpleName}") }
    }

    // Android 8–11: автоперехода нет — переходим сами, когда человек уходит из приложения («Домой», недавние приложения).
    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (Build.VERSION.SDK_INT < 31 && pipSupported && PipModel.wanted(calls.ui.value.phase))
            runCatching { enterPictureInPictureMode(pipParams(calls.ui.value)) }.onFailure { Log.w("PGLIFE", "pip_enter ${it.javaClass.simpleName}") }   // выключено в настройках — останется фон
    }

    override fun onPictureInPictureModeChanged(isInPictureInPictureMode: Boolean, newConfig: Configuration) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        inPip = isInPictureInPictureMode
        lifecycleNote("pip=$isInPictureInPictureMode")
        if (isInPictureInPictureMode) calls.onBackground()
    }

    // Камера в фоне и в плавающем окне выключена (система её ограничивает, а человек ушёл из встречи взглядом); возвращается, когда встреча снова на весь экран.
    // Включаем в onResume, а не в onStart: после плавающего окна onStart бывает и при разблокировке экрана, когда встреча всё ещё в окне. Звук продолжает служба CallService.
    override fun onStop() { super.onStop(); lifecycleNote("onStop"); calls.onBackground() }
    override fun onStart() { super.onStart(); lifecycleNote("onStart") }
    override fun onResume() { super.onResume(); if (!isInPictureInPictureMode) calls.onForeground() }
    override fun onDestroy() { lifecycleNote("onDestroy"); super.onDestroy() }

    /** Жизненный цикл в журнале устройства (тег PGLIFE, без личных данных): по нему видно, почему приложение ушло с переднего плана — завершение, смена конфигурации или обычное сворачивание. */
    private fun lifecycleNote(event: String) {
        Log.i("PGLIFE", "$event finishing=$isFinishing configChange=$isChangingConfigurations call=${calls.ui.value.phase}")
    }
}
