package org.peregovorka.android.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// Цвета — из токенов веб-интерфейса (frontend/src/tokens.css), светлая и тёмная темы. Без градиентов и размытия: экономия батареи.
private val Light = lightColorScheme(
    primary = Color(0xFF2456D3), onPrimary = Color.White, primaryContainer = Color(0xFFE4ECFD), onPrimaryContainer = Color(0xFF0F2A6B),
    background = Color(0xFFF3F5F8), onBackground = Color(0xFF161B26), surface = Color.White, onSurface = Color(0xFF161B26),
    surfaceVariant = Color(0xFFEAEEF4), onSurfaceVariant = Color(0xFF576275), outline = Color(0xFFB9C2D0), outlineVariant = Color(0xFFD6DCE6),
    error = Color(0xFFCC3535), onError = Color.White, errorContainer = Color(0xFFFBE4E1), onErrorContainer = Color(0xFFA52A22),
    secondaryContainer = Color(0xFFE4ECFD), onSecondaryContainer = Color(0xFF0F2A6B),
)
private val Dark = darkColorScheme(
    primary = Color(0xFF6F98FF), onPrimary = Color(0xFF0A1226), primaryContainer = Color(0xFF1B2A4C), onPrimaryContainer = Color(0xFFD7E2FF),
    background = Color(0xFF0E1218), onBackground = Color(0xFFE9EDF3), surface = Color(0xFF161B24), onSurface = Color(0xFFE9EDF3),
    surfaceVariant = Color(0xFF1E2530), onSurfaceVariant = Color(0xFF9BA6B8), outline = Color(0xFF3C4859), outlineVariant = Color(0xFF2B3441),
    error = Color(0xFFE0605A), onError = Color(0xFF2B0A08), errorContainer = Color(0xFF3A1F1D), onErrorContainer = Color(0xFFF4A9A3),
    secondaryContainer = Color(0xFF1B2A4C), onSecondaryContainer = Color(0xFFD7E2FF),
)

val OkColor = Color(0xFF1F9D55)
val CallBg = Color(0xFF0B0F15)

@Composable
fun PeregovorkaTheme(content: @Composable () -> Unit) {
    val base = Typography()
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) Dark else Light,
        typography = base.copy(
            titleLarge = base.titleLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 22.sp),
            titleMedium = base.titleMedium.copy(fontWeight = FontWeight.SemiBold),
            labelLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 15.sp),
        ),
        content = content,
    )
}
