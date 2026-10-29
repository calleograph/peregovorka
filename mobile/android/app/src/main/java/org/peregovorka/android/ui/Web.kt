package org.peregovorka.android.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.delay
import org.peregovorka.android.data.Server
import org.peregovorka.android.data.Store

@SuppressLint("SetJavaScriptEnabled")
private fun secureWebView(ctx: android.content.Context, origin: String, onExternal: (Uri) -> Unit): WebView = WebView(ctx).apply {
    settings.javaScriptEnabled = true          // сайт Peregovorka — одностраничное приложение
    settings.domStorageEnabled = true
    settings.allowFileAccess = false
    settings.allowContentAccess = false
    settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
    settings.setSupportMultipleWindows(false)
    CookieManager.getInstance().setAcceptThirdPartyCookies(this, false)
    webViewClient = object : WebViewClient() {
        // Переходы только внутри своего сервера; остальное — во внешний браузер (безопасное открытие внешних ссылок).
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val u = request.url
            val same = "${u.scheme}://${u.authority}" == origin
            if (!same) onExternal(u)
            return !same
        }

        // Процесс отрисовки может быть убит системой при нехватке памяти: без этого обработчика падает всё приложение, в том числе идущая конференция.
        override fun onRenderProcessGone(view: WebView, detail: android.webkit.RenderProcessGoneDetail): Boolean {
            (view.parent as? android.view.ViewGroup)?.removeView(view)
            view.destroy()
            return true
        }
    }
}

/** Страница входа сервера. Вход выполняет сам сервер (домен, TOTP, CAPTCHA, SSO): приложение не знает пароль и не обходит ограничения. Как только cookie сессии появилась и сервер её принял — вход завершён. */
@Composable
fun LoginScreen(server: Server, cookieName: String, onCookie: suspend (String) -> Boolean, onBack: () -> Unit) {
    val ctx = LocalContext.current
    var web by remember { mutableStateOf<WebView?>(null) }
    BackHandler { if (web?.canGoBack() == true) web?.goBack() else onBack() }
    LaunchedEffect(server.id) {
        val cm = CookieManager.getInstance()
        cm.removeAllCookies(null)      // чужая прежняя сессия не должна подхватиться
        while (true) {
            delay(1000)
            val v = cm.getCookie(server.url)?.split(";")?.map { it.trim() }?.firstOrNull { it.startsWith("$cookieName=") }?.substringAfter('=')
            if (!v.isNullOrEmpty() && onCookie(v)) break
        }
    }
    Column(Modifier.fillMaxSize().systemBarsPadding().imePadding()) {
        Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "К списку серверов") }
            Column { Text("Вход", style = MaterialTheme.typography.titleMedium); Text(server.title, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        AndroidView(
            modifier = Modifier.weight(1f).fillMaxWidth(),
            factory = { c -> secureWebView(c, server.url) { ctx.startActivity(Intent(Intent.ACTION_VIEW, it)) }.also { w -> web = w; w.loadUrl(server.url + "/") } },
            onRelease = { it.destroy() },
        )
    }
}

/** Раздел сайта (календарь, чаты, история) внутри приложения, с сессией выбранного сервера. */
@Composable
fun SectionWebView(server: Server, store: Store, path: String, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    var web by remember { mutableStateOf<WebView?>(null) }
    BackHandler(enabled = web?.canGoBack() == true) { web?.goBack() }
    AndroidView(
        modifier = modifier.fillMaxSize(),
        factory = { c ->
            val cm = CookieManager.getInstance()
            cm.removeAllCookies(null)
            store.cookie(server.id)?.let { cm.setCookie(server.url, "${store.cookieName(server.id)}=$it; Path=/; Secure; HttpOnly; SameSite=Lax") }
            cm.flush()
            secureWebView(c, server.url) { ctx.startActivity(Intent(Intent.ACTION_VIEW, it)) }.also { w -> web = w; w.loadUrl(server.url + path) }
        },
        update = { w -> if (w.tag != path) { w.tag = path; if (w.url == null || !w.url!!.endsWith(path)) w.loadUrl(server.url + path) } },
        onRelease = { it.destroy() },
    )
}
