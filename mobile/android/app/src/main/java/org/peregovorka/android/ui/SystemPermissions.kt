package org.peregovorka.android.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

/** Проверки и переход в настройки на настоящем Android. */
class SystemPermissions(private val ctx: Context) {
    fun granted(p: Perm): Boolean = android.os.Build.VERSION.SDK_INT < p.minSdk || ContextCompat.checkSelfPermission(ctx, p.manifest) == PackageManager.PERMISSION_GRANTED

    /** Система ещё покажет окно запроса (не было отказа «больше не спрашивать»). До первого запроса false — тоже, поэтому используется только после запроса. */
    fun canAskAgain(p: Perm): Boolean = activity()?.let { ActivityCompat.shouldShowRequestPermissionRationale(it, p.manifest) } ?: false

    fun access(p: Perm, asked: Boolean): Access = if (granted(p)) Access.Granted else if (!asked) Access.Denied else PermissionPlan.classify(false, canAskAgain(p))

    fun openSettings() {
        ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", ctx.packageName, null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun activity(): Activity? { var c: Context? = ctx; while (c is ContextWrapper) { if (c is Activity) return c; c = c.baseContext }; return null }
}

/** Пояснение перед общим запросом на первом запуске: что и зачем будет спрошено. */
@Composable
fun PermissionsIntro(sdk: Int, onAllow: () -> Unit, onLater: () -> Unit) {
    AlertDialog(
        onDismissRequest = onLater,
        title = { Text("Доступ для конференций") },
        text = {
            Column {
                Text("Чтобы конференции работали сразу, без запросов при каждом входе, разрешите:")
                Spacer(Modifier.height(8.dp))
                PermissionPlan.forFirstRun(sdk).forEach { p ->
                    Text("• ${p.title} — ${p.why}", style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(4.dp))
                }
                Spacer(Modifier.height(4.dp))
                Text("Камера и микрофон включаются только вами. От любого разрешения можно отказаться — тогда вы подключитесь слушателем.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { TextButton(onClick = onAllow) { Text("Продолжить") } },
        dismissButton = { TextButton(onClick = onLater) { Text("Позже") } },
    )
}
