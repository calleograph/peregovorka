import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// Версия приложения — из файла VERSION рядом с корнем репозитория клиента (отдельная нумерация, не версия сервера).
val appVersion = rootProject.file("VERSION").readText().trim()
val versionParts = appVersion.split(".").map { it.toInt() }
val signing = Properties().apply {
    // Ключи подписи — только из окружения или локального файла вне Git (docs/ANDROID_BUILD.md).
    val f = rootProject.file("signing.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
fun sign(name: String, env: String): String? = System.getenv(env) ?: signing.getProperty(name)

android {
    namespace = "org.peregovorka.android"
    compileSdk = 37

    defaultConfig {
        applicationId = "org.peregovorka.android"
        minSdk = 26
        targetSdk = 36
        // смещение нужно только проверке «обновление поверх» (update-over-test.sh): предыдущая сборка той же версии с меньшим versionCode
        versionCode = versionParts[0] * 10000 + versionParts[1] * 100 + versionParts[2] + ((findProperty("versionCodeOffset") as String?)?.toInt() ?: 0)
        versionName = appVersion
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        val store = sign("storeFile", "PEREGOVORKA_KEYSTORE")
        if (store != null) {
            create("release") {
                storeFile = file(store)
                storePassword = sign("storePassword", "PEREGOVORKA_KEYSTORE_PASSWORD")
                keyAlias = sign("keyAlias", "PEREGOVORKA_KEY_ALIAS")
                keyPassword = sign("keyPassword", "PEREGOVORKA_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Без ключа организации сборка подписывается отладочным ключом: годится для проверки, но не для публикации (docs/ANDROID_BUILD.md).
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    buildFeatures { compose = true; buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging { resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}", "META-INF/versions/9/OSGI-INF/MANIFEST.MF") }
    lint { abortOnError = true; warningsAsErrors = false; checkReleaseBuilds = true }
    // AGP 9.x регистрирует для каждого варианта с Compose задачу Compose Preview; ей нужны собранные ресурсы (ресурсный APK и R.jar) из модульного теста.
    // Без этого `gradlew tasks --all` и импорт проекта в VS Code падают. Unit-тесты проекта ресурсы Android не используют, их поведение не меняется.
    testOptions { unitTests.isReturnDefaultValues = true; unitTests.isIncludeAndroidResources = true }
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.material3)
    implementation(libs.compose.icons)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.webkit)
    implementation(libs.livekit.android)
    implementation(libs.livekit.compose)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.ui.test)
    androidTestImplementation(libs.androidx.test.ext)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.uiautomator)
    androidTestImplementation(libs.okhttp.mockwebserver)
    debugImplementation(libs.compose.ui.test.manifest)
    testImplementation(libs.junit)
    testImplementation(libs.kotlin.test.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
}
