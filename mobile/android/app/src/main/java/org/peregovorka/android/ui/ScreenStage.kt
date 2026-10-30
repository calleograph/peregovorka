package org.peregovorka.android.ui

import androidx.compose.animation.core.animate
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material.icons.filled.FullscreenExit
import androidx.compose.material.icons.filled.ZoomOutMap
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.unit.dp
import io.livekit.android.compose.ui.ScaleType
import io.livekit.android.compose.ui.VideoTrackView
import io.livekit.android.room.Room
import kotlinx.coroutines.launch
import org.peregovorka.android.call.CallManager
import org.peregovorka.android.call.CallUi
import org.peregovorka.android.call.Tile

/**
 * Демонстрация экрана участника: главная область конференции. Картинка вписывается с сохранением пропорций и занимает всё свободное место; её можно приближать
 * двумя пальцами (до 8×), двигать пальцем, быстро приближать двойным нажатием и возвращать целиком (повторное нажатие или кнопка). Жесты действуют только на эту область, а не
 * на весь интерфейс; поток не пересоздаётся (меняется лишь преобразование слоя), поэтому звук и подключение не прерываются.
 * Полноэкранный режим скрывает всё, кроме картинки: панель управления показывается по касанию и прячется сама (см. [CallScreen]).
 */
@Composable
fun ScreenStage(calls: CallManager, s: CallUi, room: Room, fullscreen: Boolean, onFullscreen: (Boolean) -> Unit, onTap: () -> Unit, modifier: Modifier = Modifier) {
    val sharers = s.tiles.filter { it.screen != null }
    var selectedKey by rememberSaveable { mutableStateOf("") }
    val selected = sharers.firstOrNull { it.key == selectedKey } ?: sharers.first()
    val landscape = LocalConfiguration.current.let { it.screenWidthDp > it.screenHeightDp }
    val main: @Composable (Modifier) -> Unit = { m -> ZoomableScreen(calls, room, selected, fullscreen, onFullscreen, onTap, m) }
    // у каждого показа — свой масштаб: переключение на другой экран начинается с целого
    val strip: List<Tile> = s.tiles
    if (fullscreen) { main(modifier.fillMaxSize()); return }
    if (landscape) {
        Row(modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            main(Modifier.weight(1f).fillMaxHeight())
            LazyColumn(Modifier.width(96.dp).fillMaxHeight(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                items(strip, key = { it.key }) { t -> StripTile(t, room, sharers.size > 1 && t.screen != null && t.key != selected.key, Modifier.height(88.dp).fillMaxWidth()) { selectedKey = t.key } }
            }
        }
    } else {
        Column(modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            main(Modifier.weight(1f).fillMaxWidth())
            LazyRow(Modifier.height(72.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(strip, key = { it.key }) { t -> StripTile(t, room, sharers.size > 1 && t.screen != null && t.key != selected.key, Modifier.fillMaxHeight().aspectRatio(0.9f)) { selectedKey = t.key } }
            }
        }
    }
}

/** Миниатюра участника в ленте; у другого показа экрана — подпись «экран» и выбор крупно по нажатию. */
@Composable
private fun StripTile(t: Tile, room: Room, selectable: Boolean, modifier: Modifier, onSelect: () -> Unit) {
    Box(modifier.clip(RoundedCornerShape(10.dp)).background(Color(0xFF1B2230)).then(if (selectable) Modifier.clickable(onClickLabel = "Смотреть экран крупно", onClick = onSelect) else Modifier)) {
        val track = if (selectable) t.screen else t.camera
        if (track != null) VideoTrackView(videoTrack = track, passedRoom = room, mirror = t.isLocal && !selectable, scaleType = if (selectable) ScaleType.FitInside else ScaleType.Fill, modifier = Modifier.fillMaxSize())
        else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Box(Modifier.size(36.dp).clip(CircleShape).background(Color(0xFF2B3A5E)), contentAlignment = Alignment.Center) { Text(t.name.trim().take(1).uppercase(), color = Color.White, style = MaterialTheme.typography.titleMedium) }
        }
        Text(if (selectable) "${t.name.take(14)} · экран" else if (t.isLocal) "вы" else t.name.take(12), color = Color.White, style = MaterialTheme.typography.labelSmall, maxLines = 1,
             modifier = Modifier.align(Alignment.BottomStart).padding(4.dp).clip(RoundedCornerShape(6.dp)).background(Color(0x99000000)).padding(horizontal = 5.dp, vertical = 1.dp))
    }
}

@Composable
private fun ZoomableScreen(calls: CallManager, room: Room, t: Tile, fullscreen: Boolean, onFullscreen: (Boolean) -> Unit, onTap: () -> Unit, modifier: Modifier) {
    val source = if (t.screenW > 0 && t.screenH > 0) Size2(t.screenW.toFloat(), t.screenH.toFloat()) else null
    // увеличили — просим у сервера слой в полном разрешении (иначе приближенный текст размыт); вернули целиком — выбор слоя снова по размеру окна
    ZoomSurface(
        key = t.key, source = source, label = "${t.name} · экран", fullscreen = fullscreen, onFullscreen = onFullscreen, onTap = onTap, modifier = modifier,
        onZoomChanged = { z, c ->
            if (c.w <= 0f) return@ZoomSurface
            if (z.zoomed) { val r = ZoomMath.requestedSize(source, c, 4f); calls.setScreenResolution(t.key, r.w.toInt(), r.h.toInt()) }
            else calls.setScreenResolution(t.key, 0, 0, c.w.toInt(), c.h.toInt())
        },
    ) { VideoTrackView(videoTrack = t.screen!!, passedRoom = room, scaleType = ScaleType.FitInside, modifier = Modifier.fillMaxSize()) }
}

/**
 * Область с приближением жестами (содержимое — любое: видео демонстрации, в проверках — цветной блок). Жесты применяются к преобразованию слоя содержимого, а не к его размеру:
 * поток и соединение не затрагиваются. Состояние приближения доступно службам специальных возможностей (stateDescription) и проверкам.
 */
@Composable
fun ZoomSurface(
    key: Any, source: Size2?, label: String, fullscreen: Boolean, onFullscreen: (Boolean) -> Unit, onTap: () -> Unit, modifier: Modifier = Modifier,
    onZoomChanged: (ZoomState, Size2) -> Unit = { _, _ -> }, content: @Composable () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var zoom by remember(key) { mutableStateOf(ZoomState()) }
    var container by remember { mutableStateOf(Size2(0f, 0f)) }
    // В обработчиках жестов состояние читается напрямую (а не через rememberUpdatedState): несколько событий касания могут прийти до следующей перекомпоновки,
    // и устаревшее значение «съедало» бы накопленное приближение (каждое событие считалось бы от старого масштаба).
    fun contentNow() = ZoomMath.fitted(container, source)
    val changed by rememberUpdatedState(onZoomChanged)

    fun animateTo(target: ZoomState) {
        val from = zoom
        scope.launch {
            animate(0f, 1f, animationSpec = tween(220)) { v, _ ->
                zoom = ZoomState(from.scale + (target.scale - from.scale) * v, from.offsetX + (target.offsetX - from.offsetX) * v, from.offsetY + (target.offsetY - from.offsetY) * v)
            }
            zoom = target
        }
    }
    LaunchedEffect(key, zoom.zoomed, container, source) { changed(zoom, container) }

    Box(
        modifier.clip(RoundedCornerShape(if (fullscreen) 0.dp else 12.dp)).background(Color.Black).clipToBounds()
            .onSizeChanged { sz ->
                val nc = Size2(sz.width.toFloat(), sz.height.toFloat())
                val old = container; val oldContent = contentNow()
                container = nc
                // поворот телефона, переход в полноэкранный режим: сохраняем рассматриваемую область, если возможно, иначе — целиком
                if (old.w > 0f && zoom.zoomed) zoom = ZoomMath.resize(zoom, old, oldContent, nc, ZoomMath.fitted(nc, source))
            }
            .pointerInput(key) {
                detectTransformGestures { centroid, pan, factor, _ ->
                    zoom = ZoomMath.transform(zoom, centroid.x, centroid.y, factor, pan.x, pan.y, container, contentNow())
                }
            }
            .pointerInput(key) {
                detectTapGestures(
                    onDoubleTap = { p -> animateTo(ZoomMath.doubleTap(zoom, p.x, p.y, container, contentNow())) },
                    onTap = { onTap() },
                )
            }
            .semantics { stateDescription = "масштаб %.2f, сдвиг %d, %d".format(zoom.scale, zoom.offsetX.toInt(), zoom.offsetY.toInt()) },
    ) {
        Box(Modifier.fillMaxSize().graphicsLayer { scaleX = zoom.scale; scaleY = zoom.scale; translationX = zoom.offsetX; translationY = zoom.offsetY }) { content() }
        Text(label, color = Color.White, style = MaterialTheme.typography.labelMedium, maxLines = 1,
             modifier = Modifier.align(Alignment.BottomStart).padding(8.dp).clip(RoundedCornerShape(8.dp)).background(Color(0x99000000)).padding(horizontal = 8.dp, vertical = 3.dp))
        Row(Modifier.align(Alignment.TopEnd).padding(6.dp), verticalAlignment = Alignment.CenterVertically) {
            if (zoom.zoomed) {
                Text("×%.1f".format(zoom.scale).replace('.', ','), color = Color.White, style = MaterialTheme.typography.labelMedium,
                     modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(Color(0x99000000)).padding(horizontal = 8.dp, vertical = 4.dp))
                IconButton(onClick = { animateTo(ZoomState()) }, modifier = Modifier.size(44.dp)) { Icon(Icons.Filled.ZoomOutMap, "Показать целиком", tint = Color.White) }
            }
            IconButton(onClick = { onFullscreen(!fullscreen) }, modifier = Modifier.size(44.dp)) {
                Icon(if (fullscreen) Icons.Filled.FullscreenExit else Icons.Filled.Fullscreen, if (fullscreen) "Выйти из полноэкранного режима" else "На весь экран", tint = Color.White)
            }
        }
    }
}
