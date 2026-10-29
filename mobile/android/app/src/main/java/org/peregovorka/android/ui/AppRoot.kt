package org.peregovorka.android.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.peregovorka.android.BuildConfig
import org.peregovorka.android.call.CallManager
import org.peregovorka.android.call.Phase
import org.peregovorka.android.data.*

private data class JoinTarget(val roomId: String, val name: String, val hasPassword: Boolean, val cameraAllowed: Boolean)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppRoot(vm: AppViewModel, calls: CallManager, store: Store, incomingLink: String?, onLinkHandled: () -> Unit, onMinimize: () -> Unit) {
    val s by vm.state.collectAsStateWithLifecycle()
    val call by calls.ui.collectAsStateWithLifecycle()
    val ctx = LocalContext.current

    if (call.phase != Phase.Idle) {
        CallScreen(calls, onMinimize = onMinimize, onClose = calls::dismissEnded)
        return
    }

    var target by remember { mutableStateOf<JoinTarget?>(null) }
    var pendingJoin by remember { mutableStateOf<Triple<String?, Boolean, Boolean>?>(null) }
    var pendingRef by remember { mutableStateOf<String?>(null) }
    var linkDialog by remember { mutableStateOf(false) }
    var toast by remember { mutableStateOf<String?>(null) }
    var unknownServer by remember { mutableStateOf<String?>(null) }
    var switchTo by remember { mutableStateOf<Server?>(null) }

    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { _ ->
        // Решение по каждому разрешению принимает система; без микрофона человек подключается слушателем и может включить его позже.
        val t = target ?: return@rememberLauncherForActivityResult
        val (pwd, mic, cam) = pendingJoin ?: return@rememberLauncherForActivityResult
        val micOk = ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        val camOk = ContextCompat.checkSelfPermission(ctx, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        vm.join(t.roomId, pwd, mic && micOk, cam && camOk) { ok -> if (ok) { target = null; pendingJoin = null } }
    }
    fun startJoin(pwd: String?, mic: Boolean, cam: Boolean) {
        pendingJoin = Triple(pwd, mic, cam)
        val need = buildList {
            if (mic) add(Manifest.permission.RECORD_AUDIO)
            if (cam) add(Manifest.permission.CAMERA)
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
            if (Build.VERSION.SDK_INT >= 31) add(Manifest.permission.BLUETOOTH_CONNECT)
        }.filter { ContextCompat.checkSelfPermission(ctx, it) != PackageManager.PERMISSION_GRANTED }
        if (need.isEmpty()) perms.launch(emptyArray()) else perms.launch(need.toTypedArray())
    }

    // ссылка из письма или «Подключиться по ссылке»
    fun handleLink(raw: String?) {
        when (val r = vm.openLink(raw)) {
            LinkResult.Invalid -> toast = "Ссылка не похожа на ссылку Peregovorka."
            is LinkResult.UnknownServer -> unknownServer = r.origin
            is LinkResult.Room -> pendingRef = r.ref
            is LinkResult.External -> ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(r.url)))
        }
    }
    LaunchedEffect(incomingLink) { if (incomingLink != null) { handleLink(incomingLink); onLinkHandled() } }
    LaunchedEffect(pendingRef, s.rooms) {
        val ref = pendingRef ?: return@LaunchedEffect
        s.rooms.firstOrNull { it.slug == ref || it.id == ref }?.let { r -> target = JoinTarget(r.id, r.name, r.hasPassword, r.cameraAllowed); pendingRef = null }
    }

    Box(Modifier.fillMaxSize()) {
        when (s.step) {
            Step.Boot -> Box(Modifier.fillMaxSize())
            Step.Welcome -> WelcomeScreen(onAdd = vm::startAdd)
            Step.AddServer -> AddServerScreen(s.adding, s.error, s.added, canCancel = s.servers.isNotEmpty(), onCheck = vm::checkServer, onContinue = vm::continueToLogin, onCancel = vm::cancelAdd)
            Step.Login -> s.server?.let { sv -> LoginScreen(sv, store.cookieName(sv.id), onCookie = { vm.adoptCookie(it) }, onBack = { if (s.servers.size > 1) vm.selectTab(Tab.Profile).also { vm.startAdd() } else vm.startAdd() }) }
            Step.Main -> MainScaffold(s, vm, store, onJoinRoom = { r -> vm.clearJoinError(); target = JoinTarget(r.id, r.name, r.hasPassword, r.cameraAllowed) },
                onJoinUpcoming = { u -> vm.clearJoinError(); target = JoinTarget(u.id, u.name, u.hasPassword, true) },
                onLink = { linkDialog = true }, onSwitch = { sv -> if (vm.canSwitchWithoutAsking()) vm.switchTo(sv) else switchTo = sv })
        }
        toast?.let { t -> Snackbar(Modifier.align(androidx.compose.ui.Alignment.BottomCenter).padding(16.dp).navigationBarsPadding(), action = { TextButton(onClick = { toast = null }) { Text("OK") } }) { Text(t) } }
    }

    target?.let { t ->
        JoinSheet(t.name, t.hasPassword, t.cameraAllowed, s.joining, s.joinError, onDismiss = { target = null; vm.clearJoinError() }, onJoin = { pwd, mic, cam -> startJoin(pwd, mic, cam) })
    }
    if (linkDialog) {
        var text by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { linkDialog = false }, title = { Text("Подключиться по ссылке") },
            text = { OutlinedTextField(value = text, onValueChange = { text = it.take(2048) }, singleLine = true, placeholder = { Text("https://meeting.example.org/rooms/…") }) },
            confirmButton = { TextButton(onClick = { linkDialog = false; handleLink(text.trim()) }, enabled = text.isNotBlank()) { Text("Открыть") } },
            dismissButton = { TextButton(onClick = { linkDialog = false }) { Text("Отмена") } },
        )
    }
    unknownServer?.let { o ->
        AlertDialog(
            onDismissRequest = { unknownServer = null }, title = { Text("Сервер не добавлен") },
            text = { Text("Ссылка ведёт на $o, которого нет в списке. Добавьте сервер и войдите на нём.") },
            confirmButton = { TextButton(onClick = { unknownServer = null; vm.startAdd() }) { Text("Добавить") } },
            dismissButton = { TextButton(onClick = { unknownServer = null }) { Text("Отмена") } },
        )
    }
    switchTo?.let { sv ->
        AlertDialog(
            onDismissRequest = { switchTo = null }, title = { Text("Прервать конференцию?") },
            text = { Text("Переход на «${sv.title}» завершит текущую конференцию.") },
            confirmButton = { TextButton(onClick = { switchTo = null; vm.switchTo(sv) }) { Text("Перейти", color = MaterialTheme.colorScheme.error) } },
            dismissButton = { TextButton(onClick = { switchTo = null }) { Text("Остаться") } },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainScaffold(
    s: UiState, vm: AppViewModel, store: Store, onJoinRoom: (RoomDto) -> Unit, onJoinUpcoming: (UpcomingItem) -> Unit, onLink: () -> Unit, onSwitch: (Server) -> Unit,
) {
    val wide = LocalConfiguration.current.screenWidthDp >= 600
    val server = s.server ?: return
    val items = listOf(
        Triple(Tab.Home, "Главная", Icons.Filled.Home), Triple(Tab.Calendar, "Календарь", Icons.Filled.CalendarMonth), Triple(Tab.Chats, "Чаты", Icons.Filled.Chat),
        Triple(Tab.History, "История", Icons.Filled.History), Triple(Tab.Profile, "Профиль", Icons.Filled.Person),
    )
    Row(Modifier.fillMaxSize()) {
        if (wide) NavigationRail {
            items.forEach { (t, label, icon) -> NavigationRailItem(selected = s.tab == t, onClick = { vm.selectTab(t) }, icon = { Icon(icon, null) }, label = { Text(label) }) }
        }
        Scaffold(
            modifier = Modifier.weight(1f),
            topBar = {
                TopAppBar(
                    title = { Column { Text(items.first { it.first == s.tab }.second, style = MaterialTheme.typography.titleLarge); Text(server.title, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) } },
                    actions = { if (s.tab == Tab.Home) IconButton(onClick = onLink) { Icon(Icons.Filled.Link, "Подключиться по ссылке") } },
                )
            },
            bottomBar = {
                if (!wide) NavigationBar {
                    items.forEach { (t, label, icon) -> NavigationBarItem(selected = s.tab == t, onClick = { vm.selectTab(t) }, icon = { Icon(icon, null) }, label = { Text(label, maxLines = 1) }, alwaysShowLabel = true) }
                }
            },
        ) { pad ->
            val m = Modifier.padding(pad)
            when (s.tab) {
                Tab.Home -> HomeContent(s.rooms, s.upcoming, s.loading, s.error, vm::refresh, onJoinRoom, onJoinUpcoming, m)
                Tab.Calendar -> SectionWebView(server, store, "/calendar", m)
                Tab.Chats -> SectionWebView(server, store, "/", m)
                Tab.History -> SectionWebView(server, store, "/history", m)
                Tab.Profile -> ProfileContent(s.me, s.servers, server, BuildConfig.VERSION_NAME, onSwitch, vm::rename, vm::remove, vm::startAdd, vm::logout, m)
            }
        }
    }
}
