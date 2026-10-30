package org.peregovorka.android.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.peregovorka.android.data.*
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val timeFmt = DateTimeFormatter.ofPattern("d MMM, HH:mm", Locale("ru")).withZone(ZoneId.systemDefault())
fun formatStart(iso: String?): String = runCatching { timeFmt.format(Instant.parse(iso)) }.getOrDefault("")

/** Главная: предстоящие встречи, затем постоянные переговорки; «Подключиться» доступна с первого экрана. */
@Composable
fun HomeContent(
    rooms: List<RoomDto>, upcoming: List<UpcomingItem>, loading: Boolean, error: String?,
    onRefresh: () -> Unit, onJoinRoom: (RoomDto) -> Unit, onJoinUpcoming: (UpcomingItem) -> Unit, modifier: Modifier = Modifier,
) {
    var query by remember { mutableStateOf("") }
    val shown = remember(rooms, query) { rooms.filter { query.isBlank() || it.name.contains(query, true) || it.description.orEmpty().contains(query, true) } }
    val permanent = shown.filter { it.lifetime == "permanent" }
    PullToRefreshBox(isRefreshing = loading, onRefresh = onRefresh, modifier = modifier) {
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (error != null) item { Banner(error) }
            if (upcoming.isNotEmpty() && query.isBlank()) {
                item { SectionTitle("Предстоящие встречи") }
                items(upcoming.filter { !it.cancelled }, key = { "u" + it.id }) { u -> UpcomingCard(u) { onJoinUpcoming(u) } }
            }
            item {
                SectionTitle("Переговорные")
                OutlinedTextField(
                    value = query, onValueChange = { query = it }, singleLine = true, placeholder = { Text("Найти переговорную") },
                    leadingIcon = { Icon(Icons.Filled.Search, null) }, modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                )
            }
            if (permanent.isEmpty() && !loading) item { Text(if (query.isBlank()) "Переговорных пока нет." else "Ничего не найдено.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(permanent, key = { "r" + it.id }) { r -> RoomCard(r) { onJoinRoom(r) } }
        }
    }
}

@Composable private fun SectionTitle(t: String) = Text(t, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 4.dp))

@Composable
fun Banner(text: String) {
    Surface(color = MaterialTheme.colorScheme.errorContainer, contentColor = MaterialTheme.colorScheme.onErrorContainer, shape = MaterialTheme.shapes.medium) {
        Text(text, Modifier.padding(12.dp).fillMaxWidth(), style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun RoomCard(r: RoomDto, onJoin: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onJoin), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).clip(CircleShape), contentAlignment = Alignment.Center) {
                Surface(color = MaterialTheme.colorScheme.primaryContainer, modifier = Modifier.fillMaxSize()) {
                    Box(contentAlignment = Alignment.Center) { Text(r.name.take(1).uppercase(), style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onPrimaryContainer) }
                }
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(r.name, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (r.hasPassword) Icon(Icons.Filled.Lock, "Нужен пароль", Modifier.padding(start = 6.dp).size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (r.live) { Spacer(Modifier.width(6.dp)); Text("ЭФИР", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error) }
                }
                val n = r.active?.participants ?: 0
                Text(if (n > 0) "Идёт встреча · участников: $n" else r.description?.takeIf { it.isNotBlank() } ?: "Свободна", style = MaterialTheme.typography.bodySmall,
                    color = if (n > 0) OkColor else MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            FilledTonalButton(onClick = onJoin, modifier = Modifier.heightIn(min = 44.dp)) { Text("Войти") }
        }
    }
}

@Composable
private fun UpcomingCard(u: UpcomingItem, onJoin: () -> Unit) {
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(u.name, style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                val whenText = if (u.live != null) "Идёт сейчас · участников: ${u.live.participants}" else formatStart(u.start)
                Text(listOfNotNull(whenText.ifBlank { null }, u.organizer).joinToString(" · "), style = MaterialTheme.typography.bodySmall,
                    color = if (u.live != null) OkColor else MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (u.canJoin) Button(onClick = onJoin, modifier = Modifier.heightIn(min = 44.dp)) { Text("Подключиться") }
        }
    }
}

/** Нижний лист подключения: пароль (если нужен), микрофон и камера на входе. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun JoinSheet(name: String, hasPassword: Boolean, cameraAllowed: Boolean, busy: Boolean, error: String?, onDismiss: () -> Unit, onJoin: (password: String?, mic: Boolean, camera: Boolean) -> Unit) {
    var pwd by remember { mutableStateOf("") }
    var mic by remember { mutableStateOf(true) }
    var cam by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 20.dp).navigationBarsPadding().imePadding(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(name, style = MaterialTheme.typography.titleLarge)
            if (hasPassword) OutlinedTextField(
                value = pwd, onValueChange = { pwd = it }, singleLine = true, label = { Text("Пароль комнаты") }, isError = error != null,
                visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth(),
            )
            if (error != null) Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
            ToggleRow("Микрофон включён", mic) { mic = it }
            if (cameraAllowed) ToggleRow("Камера включена", cam) { cam = it }
            Button(onClick = { onJoin(pwd.ifEmpty { null }, mic, cam && cameraAllowed) }, enabled = !busy && (!hasPassword || pwd.isNotEmpty()), modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)) {
                if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary) else Text("Подключиться")
            }
        }
    }
}

@Composable
private fun ToggleRow(label: String, on: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f)); Switch(checked = on, onCheckedChange = onChange)
    }
}

/** Профиль и серверы: текущая учётная запись, список организаций (переключение, переименование, удаление), выход. */
@Composable
fun ProfileContent(
    me: MeDto?, servers: List<Server>, current: Server?, version: String, onOpenWeb: (String) -> Unit,
    onSwitch: (Server) -> Unit, onRename: (Server, String) -> Unit, onRemove: (Server) -> Unit, onAdd: () -> Unit, onLogout: () -> Unit, modifier: Modifier = Modifier,
) {
    var renaming by remember { mutableStateOf<Server?>(null) }
    var removing by remember { mutableStateOf<Server?>(null) }
    LazyColumn(modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Column(Modifier.padding(16.dp).fillMaxWidth()) {
                    Text(me?.user?.displayName.orEmpty().ifBlank { "Профиль" }, style = MaterialTheme.typography.titleLarge)
                    Text(current?.title.orEmpty(), color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.height(10.dp))
                    // Разделы сайта, для которых нет мобильного экрана; собственная навигация сайта внутри приложения скрыта
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { onOpenWeb("/profile") }, modifier = Modifier.heightIn(min = 44.dp)) { Text("Личный кабинет") }
                        if (me?.user?.isAdmin == true) OutlinedButton(onClick = { onOpenWeb("/admin") }, modifier = Modifier.heightIn(min = 44.dp)) { Text("Администрирование") }
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = onLogout, modifier = Modifier.heightIn(min = 44.dp)) { Text("Выйти из аккаунта") }
                }
            }
        }
        item { PermissionsCard() }
        item { Text("Серверы", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 6.dp)) }
        items(servers, key = { it.id }) { s ->
            Card(Modifier.fillMaxWidth().clickable { onSwitch(s) }, colors = CardDefaults.cardColors(containerColor = if (s.id == current?.id) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface)) {
                Row(Modifier.padding(start = 16.dp, top = 4.dp, bottom = 4.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(s.title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(s.url.removePrefix("https://"), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    if (s.id == current?.id) Icon(Icons.Filled.Check, "Текущий сервер", tint = MaterialTheme.colorScheme.primary)
                    IconButton(onClick = { renaming = s }) { Icon(Icons.Filled.Edit, "Переименовать") }
                    IconButton(onClick = { removing = s }) { Icon(Icons.Filled.Delete, "Удалить сервер") }
                }
            }
        }
        item { OutlinedButton(onClick = onAdd, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Icon(Icons.Filled.Add, null); Spacer(Modifier.width(8.dp)); Text("Добавить сервер") } }
        item { Text("Peregovorka для Android $version", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp)) }
    }
    renaming?.let { s ->
        var alias by remember(s.id) { mutableStateOf(s.alias.ifBlank { s.title }) }
        AlertDialog(
            onDismissRequest = { renaming = null }, title = { Text("Название в списке") },
            text = { OutlinedTextField(value = alias, onValueChange = { alias = it.take(60) }, singleLine = true) },
            confirmButton = { TextButton(onClick = { onRename(s, alias); renaming = null }) { Text("Сохранить") } },
            dismissButton = { TextButton(onClick = { renaming = null }) { Text("Отмена") } },
        )
    }
    removing?.let { s ->
        AlertDialog(
            onDismissRequest = { removing = null }, title = { Text("Удалить сервер?") },
            text = { Text("«${s.title}» исчезнет из списка, вход на нём будет отозван.") },
            confirmButton = { TextButton(onClick = { onRemove(s); removing = null }) { Text("Удалить", color = MaterialTheme.colorScheme.error) } },
            dismissButton = { TextButton(onClick = { removing = null }) { Text("Отмена") } },
        )
    }
}

/**
 * Что разрешено приложению. Состояние перечитывается при каждом возврате в приложение: выданное или отозванное в настройках Android видно сразу, перезапуск не нужен.
 * «Разрешить» показывает системное окно; если Android его уже не показывает (отказ «больше не спрашивать»), ведёт в настройки.
 */
@Composable
private fun PermissionsCard() {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val access = remember(ctx) { SystemPermissions(ctx) }
    var tick by remember { mutableIntStateOf(0) }
    var asked by androidx.compose.runtime.saveable.rememberSaveable { mutableStateOf(setOf<String>()) }
    val lifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current.lifecycle
    DisposableEffect(lifecycle) {
        val obs = androidx.lifecycle.LifecycleEventObserver { _, e -> if (e == androidx.lifecycle.Lifecycle.Event.ON_RESUME) tick++ }
        lifecycle.addObserver(obs); onDispose { lifecycle.removeObserver(obs) }
    }
    val launcher = androidx.activity.compose.rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions()) { _ -> tick++ }
    val items = remember(tick) { PermissionPlan.forFirstRun(android.os.Build.VERSION.SDK_INT).map { it to access.granted(it) } }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp).fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Разрешения", style = MaterialTheme.typography.titleMedium)
            items.forEach { (p, ok) ->
                Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(p.title, style = MaterialTheme.typography.bodyLarge)
                        Text(if (ok) "разрешено" else p.why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (!ok) TextButton(onClick = {
                        if (p.manifest in asked && !access.canAskAgain(p)) access.openSettings()
                        else { asked = asked + p.manifest; launcher.launch(arrayOf(p.manifest)) }
                    }, modifier = Modifier.heightIn(min = 44.dp)) { Text("Разрешить") }
                    else Icon(Icons.Filled.Check, "Разрешено", tint = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}
