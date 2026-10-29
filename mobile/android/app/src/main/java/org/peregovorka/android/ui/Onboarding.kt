package org.peregovorka.android.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import org.peregovorka.android.data.ServerInfo

/** Первый запуск: приветствие и переход к добавлению сервера. */
@Composable
fun WelcomeScreen(onAdd: () -> Unit) {
    Column(
        Modifier.fillMaxSize().systemBarsPadding().padding(24.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Surface(color = MaterialTheme.colorScheme.primary, shape = MaterialTheme.shapes.large, modifier = Modifier.size(72.dp)) {
            Box(contentAlignment = Alignment.Center) { Text("P", style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.onPrimary) }
        }
        Spacer(Modifier.height(24.dp))
        Text("Добро пожаловать в Peregovorka", style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(8.dp))
        Text(
            "Приложение подключается к серверу Peregovorka вашей организации. Адрес сервера вам сообщит администратор.",
            style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(28.dp))
        Button(onClick = onAdd, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)) { Text("Добавить сервер") }
    }
}

/** Ввод адреса сервера: проверка адреса, TLS и совместимости, показ названия организации. Клавиатура не закрывает кнопку: кнопка выше клавиатуры (imePadding). */
@Composable
fun AddServerScreen(
    adding: Boolean, error: String?, found: ServerInfo?, canCancel: Boolean,
    onCheck: (String) -> Unit, onContinue: () -> Unit, onCancel: () -> Unit,
) {
    var url by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    Column(Modifier.fillMaxSize().systemBarsPadding().imePadding().padding(horizontal = 20.dp)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 56.dp), verticalAlignment = Alignment.CenterVertically) {
            if (canCancel) IconButton(onClick = onCancel) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Назад") }
            Text("Добавить сервер", style = MaterialTheme.typography.titleLarge)
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(top = 8.dp)) {
            Text("Публичный адрес сервера Peregovorka вашей организации.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(
                value = url, onValueChange = { url = it }, singleLine = true, label = { Text("Адрес сервера") }, placeholder = { Text("https://meeting.example.org") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
                keyboardActions = KeyboardActions(onGo = { if (!adding && url.isNotBlank()) onCheck(url) }),
                isError = error != null, modifier = Modifier.fillMaxWidth().focusRequester(focus),
            )
            if (error != null) { Spacer(Modifier.height(8.dp)); Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium) }
            if (found != null) {
                Spacer(Modifier.height(16.dp))
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
                    Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.CheckCircle, null, tint = OkColor)
                        Spacer(Modifier.width(12.dp))
                        Column {
                            Text(found.name.ifBlank { "Сервер Peregovorka" }, style = MaterialTheme.typography.titleMedium)
                            Text("Соединение защищено. Версия сервера ${found.serverVersion}", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
        Button(
            onClick = { if (found != null) onContinue() else onCheck(url) },
            enabled = !adding && (found != null || url.isNotBlank()),
            modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(bottom = 12.dp),
        ) {
            if (adding) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
            else Text(if (found != null) "Войти" else "Проверить")
        }
    }
}
