package org.peregovorka.android.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import io.livekit.android.compose.ui.ScaleType
import io.livekit.android.compose.ui.VideoTrackView
import org.peregovorka.android.call.*

/** Приложение сейчас в плавающем окне (системная «картинка в картинке»): экран встречи показывает только одно изображение, без кнопок и панелей. */
val LocalInPip = compositionLocalOf { false }

/** Содержимое плавающего окна: демонстрация экрана, говорящий или собеседник (правила — PipModel.focus); снизу — имя и выключенный микрофон. */
@Composable
fun PipView(calls: CallManager, s: CallUi) {
    val spoke = remember { arrayOfNulls<String>(1) }                     // последний говоривший — не состояние Compose: смена не вызывает лишней перерисовки
    val slots = s.tiles.map { it.pipSlot() }
    spoke[0] = PipModel.lastSpeaker(slots, spoke[0])
    val key = PipModel.focus(slots, spoke[0])
    val t = s.tiles.firstOrNull { it.key == key }
    val room = calls.room()
    Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
        when {
            s.phase == Phase.Ended -> Note("Встреча завершена")
            t == null || room == null -> Note("Подключение…")
            t.screen != null && !t.isLocal -> VideoTrackView(videoTrack = t.screen, passedRoom = room, scaleType = ScaleType.FitInside, modifier = Modifier.fillMaxSize())
            t.camera != null -> VideoTrackView(videoTrack = t.camera, passedRoom = room, mirror = t.isLocal, scaleType = ScaleType.Fill, modifier = Modifier.fillMaxSize())
            else -> Box(Modifier.size(48.dp).clip(CircleShape).background(Color(0xFF2B3A5E)), contentAlignment = Alignment.Center) {
                Text(t.name.trim().take(1).uppercase(), color = Color.White, style = MaterialTheme.typography.titleLarge)
            }
        }
        if (t != null && s.phase != Phase.Ended) Row(
            Modifier.align(Alignment.BottomStart).padding(4.dp).clip(RoundedCornerShape(6.dp)).background(Color(0x99000000)).padding(horizontal = 6.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (!t.micOn && t.screen == null) { Icon(Icons.Filled.MicOff, "Микрофон выключен", tint = Color(0xFFE0605A), modifier = Modifier.size(12.dp)); Spacer(Modifier.width(3.dp)) }
            Text(if (t.isLocal) "${t.name} (вы)" else t.name, color = Color.White, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (s.phase == Phase.Reconnecting) Text("Связь потеряна…", color = Color(0xFFE0B04A), style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.align(Alignment.TopCenter).padding(4.dp).clip(RoundedCornerShape(6.dp)).background(Color(0x99000000)).padding(horizontal = 6.dp, vertical = 2.dp))
        if (!s.micOn && s.phase != Phase.Ended) Icon(Icons.Filled.MicOff, "Ваш микрофон выключен", tint = Color.White,
            modifier = Modifier.align(Alignment.TopEnd).padding(4.dp).clip(CircleShape).background(Color(0xCCCC3535)).padding(3.dp).size(12.dp))
    }
}

@Composable
private fun Note(text: String) = Text(text, color = Color(0xFFBBC3D0), style = MaterialTheme.typography.labelMedium)
