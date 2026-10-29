package org.peregovorka.android.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.livekit.android.compose.ui.ScaleType
import io.livekit.android.compose.ui.VideoTrackView
import kotlinx.coroutines.delay
import org.peregovorka.android.call.*
import kotlin.math.ceil

private enum class Sheet { Chat, People, More }

/** Экран конференции: тёмный фон, сетка участников (или демонстрация экрана на всю область), ряд кнопок управления внизу — без прокрутки. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CallScreen(calls: CallManager, onMinimize: () -> Unit, onClose: () -> Unit) {
    val s by calls.ui.collectAsStateWithLifecycle()
    var sheet by remember { mutableStateOf<Sheet?>(null) }
    var confirmLeave by remember { mutableStateOf(false) }
    val view = LocalView.current
    DisposableEffect(Unit) { view.keepScreenOn = true; onDispose { view.keepScreenOn = false } }
    BackHandler { if (sheet != null) sheet = null else onMinimize() }     // «Назад» сворачивает приложение, конференция продолжается; выйти — красной кнопкой
    LaunchedEffect(s.phase) { while (s.phase == Phase.Connected || s.phase == Phase.Reconnecting) { calls.refreshChat(); delay(if (s.chatOpen) 3000 else 10_000) } }

    if (s.phase == Phase.Ended) {
        Column(Modifier.fillMaxSize().background(CallBg).systemBarsPadding().padding(24.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Вы вышли из конференции", color = Color.White, style = MaterialTheme.typography.titleLarge)
            s.endReason?.let { Spacer(Modifier.height(8.dp)); Text(it, color = Color(0xFFBBC3D0)) }
            Spacer(Modifier.height(24.dp))
            Button(onClick = onClose, modifier = Modifier.heightIn(min = 52.dp)) { Text("Закрыть") }
        }
        return
    }

    Column(Modifier.fillMaxSize().background(CallBg).systemBarsPadding()) {
        CallTopBar(s)
        Box(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 8.dp)) { Stage(calls, s) }
        Controls(
            s, onMic = calls::toggleMic, onCam = calls::toggleCamera, onHand = calls::toggleHand,
            onChat = { sheet = Sheet.Chat; calls.setChatOpen(true) }, onPeople = { sheet = Sheet.People }, onMore = { sheet = Sheet.More }, onLeave = { confirmLeave = true },
        )
    }
    if (sheet != null) ModalBottomSheet(onDismissRequest = { if (sheet == Sheet.Chat) calls.setChatOpen(false); sheet = null }, containerColor = MaterialTheme.colorScheme.surface) {
        when (sheet) {
            Sheet.Chat -> ChatSheet(s, calls)
            Sheet.People -> PeopleSheet(s, calls)
            Sheet.More -> MoreSheet(s, calls) { sheet = null }
            null -> {}
        }
    }
    if (confirmLeave) AlertDialog(
        onDismissRequest = { confirmLeave = false }, title = { Text("Выйти из конференции?") },
        confirmButton = { TextButton(onClick = { confirmLeave = false; calls.leave() }) { Text("Выйти", color = MaterialTheme.colorScheme.error) } },
        dismissButton = { TextButton(onClick = { confirmLeave = false }) { Text("Остаться") } },
    )
}

@Composable
private fun CallTopBar(s: CallUi) {
    var elapsed by remember { mutableLongStateOf(0) }
    LaunchedEffect(s.startedAt) { while (true) { elapsed = (System.currentTimeMillis() - s.startedAt) / 1000; delay(1000) } }
    Row(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(s.roomName, color = Color.White, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                when (s.phase) { Phase.Connecting -> "Подключение…"; Phase.Reconnecting -> "Связь потеряна, восстанавливаем…"; else -> s.serverTitle },
                color = if (s.phase == Phase.Reconnecting) Color(0xFFE0B04A) else Color(0xFF9BA6B8), style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        if (s.e2ee) Icon(Icons.Filled.Lock, "Сквозное шифрование", tint = OkColor, modifier = Modifier.padding(end = 10.dp).size(18.dp))
        Text("%d:%02d".format(elapsed / 60, elapsed % 60), color = Color(0xFF9BA6B8), style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun Stage(calls: CallManager, s: CallUi) {
    val room = calls.room()
    val sharer = s.tiles.firstOrNull { it.screen != null }
    if (s.tiles.isEmpty() || room == null) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = Color.White) }; return }
    if (sharer != null) {
        // Демонстрация экрана занимает всю доступную площадь; участники — узкой лентой под ней.
        Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Box(Modifier.weight(1f).fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Color.Black)) {
                VideoTrackView(videoTrack = sharer.screen!!, passedRoom = room, scaleType = ScaleType.FitInside, modifier = Modifier.fillMaxSize())
                NameChip(sharer.name + " · экран", sharer.micOn, Modifier.align(Alignment.BottomStart).padding(8.dp))
            }
            LazyRow(Modifier.height(96.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(s.tiles, key = { it.key }) { t -> TileView(t, room, Modifier.fillMaxHeight().aspectRatio(0.8f)) }
            }
        }
        return
    }
    val landscape = LocalConfiguration.current.screenWidthDp > LocalConfiguration.current.screenHeightDp
    val n = s.tiles.size
    val cols = when { n == 1 -> 1; landscape -> if (n <= 2) 2 else if (n <= 6) 3 else 4; n <= 4 -> 2; else -> 2 }
    val rows = ceil(n / cols.toDouble()).toInt()
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val gap = 6.dp
        val fit = (maxHeight - gap * (rows - 1)) / rows
        val h = maxOf(fit, 150.dp)
        LazyVerticalGrid(GridCells.Fixed(cols), Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(gap), verticalArrangement = Arrangement.spacedBy(gap)) {
            items(s.tiles, key = { it.key }) { t -> TileView(t, room, Modifier.height(h)) }
        }
    }
}

@Composable
private fun TileView(t: Tile, room: io.livekit.android.room.Room, modifier: Modifier) {
    Box(modifier.clip(RoundedCornerShape(12.dp)).background(Color(0xFF1B2230)).then(if (t.speaking) Modifier.border(BorderStroke(2.dp, Color(0xFF6F98FF)), RoundedCornerShape(12.dp)) else Modifier)) {
        if (t.camera != null) VideoTrackView(videoTrack = t.camera, passedRoom = room, mirror = t.isLocal, scaleType = ScaleType.Fill, modifier = Modifier.fillMaxSize())
        else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Box(Modifier.size(56.dp).clip(CircleShape).background(Color(0xFF2B3A5E)), contentAlignment = Alignment.Center) {
                Text(t.name.trim().take(1).uppercase(), color = Color.White, style = MaterialTheme.typography.titleLarge)
            }
        }
        NameChip(if (t.isLocal) "${t.name} (вы)" else t.name, t.micOn, Modifier.align(Alignment.BottomStart).padding(6.dp))
        if (t.hand) Icon(Icons.Filled.PanTool, "Поднята рука", tint = Color(0xFFE0B04A), modifier = Modifier.align(Alignment.TopEnd).padding(8.dp).size(20.dp))
    }
}

@Composable
private fun NameChip(name: String, micOn: Boolean, modifier: Modifier) {
    Row(modifier.clip(RoundedCornerShape(8.dp)).background(Color(0x99000000)).padding(horizontal = 8.dp, vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
        if (!micOn) { Icon(Icons.Filled.MicOff, "Микрофон выключен", tint = Color(0xFFE0605A), modifier = Modifier.size(14.dp)); Spacer(Modifier.width(4.dp)) }
        Text(name, color = Color.White, style = MaterialTheme.typography.labelMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun Controls(s: CallUi, onMic: () -> Unit, onCam: () -> Unit, onHand: () -> Unit, onChat: () -> Unit, onPeople: () -> Unit, onMore: () -> Unit, onLeave: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
        CtrlButton(if (s.micOn) Icons.Filled.Mic else Icons.Filled.MicOff, if (s.micOn) "Выключить микрофон" else "Включить микрофон", on = s.micOn, enabled = s.canMic, onClick = onMic)
        CtrlButton(if (s.camOn) Icons.Filled.Videocam else Icons.Filled.VideocamOff, if (s.camOn) "Выключить камеру" else "Включить камеру", on = s.camOn, enabled = s.canCamera, onClick = onCam)
        CtrlButton(Icons.Filled.PanTool, if (s.handUp) "Опустить руку" else "Поднять руку", on = s.handUp, accent = true, onClick = onHand)
        CtrlButton(Icons.Filled.Chat, "Чат", badge = s.unread, onClick = onChat)
        CtrlButton(Icons.Filled.People, "Участники", badge = 0, onClick = onPeople)
        CtrlButton(Icons.Filled.MoreHoriz, "Ещё", onClick = onMore)
        CtrlButton(Icons.Filled.CallEnd, "Выйти из конференции", danger = true, onClick = onLeave)
    }
}

@Composable
private fun RowScope.CtrlButton(icon: ImageVector, label: String, on: Boolean = false, accent: Boolean = false, danger: Boolean = false, enabled: Boolean = true, badge: Int = 0, onClick: () -> Unit) {
    val bg = when { danger -> Color(0xFFCC3535); accent && on -> Color(0xFFE0B04A); !on && icon == Icons.Filled.MicOff || !on && icon == Icons.Filled.VideocamOff -> Color(0xFF3A1F1D); else -> Color(0xFF263042) }
    val fg = if (accent && on) Color(0xFF1A1405) else Color.White
    Box(Modifier.weight(1f).widthIn(max = 56.dp).heightIn(min = 48.dp).height(48.dp), contentAlignment = Alignment.Center) {
        FilledIconButton(
            onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxSize(), shape = RoundedCornerShape(14.dp),
            colors = IconButtonDefaults.filledIconButtonColors(containerColor = bg, contentColor = fg, disabledContainerColor = Color(0xFF1B2230), disabledContentColor = Color(0xFF5A6578)),
        ) { Icon(icon, label, Modifier.size(24.dp)) }
        if (badge > 0) Badge(Modifier.align(Alignment.TopEnd)) { Text(if (badge > 9) "9+" else "$badge") }
    }
}

@Composable
private fun ChatSheet(s: CallUi, calls: CallManager) {
    var text by remember { mutableStateOf("") }
    val list = rememberLazyListState()
    LaunchedEffect(s.chat.size) { if (s.chat.isNotEmpty()) list.animateScrollToItem(s.chat.lastIndex) }
    Column(Modifier.fillMaxWidth().heightIn(max = 520.dp).imePadding().navigationBarsPadding().padding(horizontal = 16.dp).padding(bottom = 8.dp)) {
        Text("Чат встречи", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(bottom = 8.dp))
        LazyColumn(Modifier.weight(1f, fill = false), state = list, verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (s.chat.isEmpty()) item { Text("Сообщений пока нет.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
            items(s.chat, key = { it.id }) { m ->
                val mine = m.authorId != null && m.authorId == calls.myId()
                Column(Modifier.fillMaxWidth(), horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
                    Surface(color = if (mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(12.dp)) {
                        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                            if (!mine) Text(m.author, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                            Text(m.text)
                        }
                    }
                }
            }
        }
        Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = text, onValueChange = { text = it.take(4000) }, modifier = Modifier.weight(1f), placeholder = { Text("Сообщение") }, maxLines = 4,
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = androidx.compose.foundation.text.KeyboardActions(onSend = { calls.sendChat(text); text = "" }),
            )
            IconButton(onClick = { calls.sendChat(text); text = "" }, enabled = text.isNotBlank(), modifier = Modifier.size(52.dp)) { Icon(Icons.AutoMirrored.Filled.Send, "Отправить") }
        }
    }
}

@Composable
private fun PeopleSheet(s: CallUi, calls: CallManager) {
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 16.dp).padding(bottom = 16.dp)) {
        Text("Участники: ${s.tiles.size}", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(bottom = 8.dp))
        LazyColumn(Modifier.heightIn(max = 420.dp)) {
            items(s.tiles, key = { it.key }) { t ->
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(if (t.isLocal) "${t.name} (вы)" else t.name, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
                    if (t.hand) Icon(Icons.Filled.PanTool, "Поднята рука", tint = Color(0xFFD19A2A), modifier = Modifier.size(20.dp).padding(end = 4.dp))
                    Icon(if (t.micOn) Icons.Filled.Mic else Icons.Filled.MicOff, if (t.micOn) "Микрофон включён" else "Микрофон выключен", Modifier.size(20.dp),
                        tint = if (t.micOn) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error)
                }
            }
        }
        OutlinedButton(onClick = calls::toggleHand, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(top = 8.dp)) { Text(if (s.handUp) "Опустить руку" else "Поднять руку") }
    }
}

@Composable
private fun MoreSheet(s: CallUi, calls: CallManager, close: () -> Unit) {
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 16.dp).padding(bottom = 16.dp)) {
        Text("Ещё", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(bottom = 8.dp))
        if (s.camOn) TextButton(onClick = { calls.flipCamera(); close() }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Icon(Icons.Filled.FlipCameraAndroid, null); Spacer(Modifier.width(12.dp)); Text("Переключить камеру", Modifier.weight(1f)) }
        val devices = remember(s.audioDevice, s.phase) { calls.audioDevices() }
        if (devices.isNotEmpty()) {
            Text("Звук", style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 8.dp))
            devices.forEach { d ->
                TextButton(onClick = { calls.selectAudio(d); close() }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                    Text(audioTitle(d.name), Modifier.weight(1f)); if (d.name == s.audioDevice) Icon(Icons.Filled.Check, "Выбрано")
                }
            }
        }
    }
}

private fun audioTitle(n: String) = when (n) { "Speakerphone" -> "Громкая связь"; "Earpiece" -> "Разговорный динамик"; "WiredHeadset" -> "Проводная гарнитура"; "BluetoothHeadset" -> "Bluetooth-гарнитура"; else -> n }
