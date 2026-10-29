pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // audioswitch (переключение динамика/Bluetooth) — зависимость LiveKit, опубликована только в JitPack.
        // Репозиторий разрешён строго для одной группы: остальные библиотеки оттуда не берутся.
        maven("https://jitpack.io") { content { includeGroup("com.github.davidliu") } }
    }
}
rootProject.name = "peregovorka-android"
include(":app")
