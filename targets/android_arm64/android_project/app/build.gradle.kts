import org.gradle.api.GradleException
import org.gradle.api.tasks.Sync

plugins {
    id("com.android.application")
}

val pythonPrefix = providers.gradleProperty("pythonPrefix").orNull
    ?.let { file(it).canonicalFile }
    ?: throw GradleException("Pass -PpythonPrefix with a CPython Android prefix.")
val pythonEntry = providers.gradleProperty("pythonEntry").orNull
    ?.let { file(it).canonicalFile }
    ?: throw GradleException("Pass -PpythonEntry with a Python entry script.")
val pythonLibDir = File(pythonPrefix, "lib")
val pythonVersion = pythonLibDir.listFiles()
    ?.asSequence()
    ?.filter { it.isDirectory }
    ?.mapNotNull { Regex("""python(\d+\.\d+[a-z]*)""").matchEntire(it.name)?.groupValues?.get(1) }
    ?.singleOrNull()
    ?: throw GradleException("Expected exactly one lib/pythonX.Y runtime under $pythonLibDir.")
val pythonRuntimeDir = File(pythonLibDir, "python$pythonVersion")
val pythonSharedLibrary = File(pythonLibDir, "libpython$pythonVersion.so")
val pythonHeaders = File(pythonPrefix, "include/python$pythonVersion/Python.h")
if (!pythonRuntimeDir.isDirectory || !pythonSharedLibrary.isFile || !pythonHeaders.isFile) {
    throw GradleException("The selected prefix is missing CPython runtime, shared library, or headers.")
}
if (pythonRuntimeDir.listFiles()?.none {
        it.name.matches(Regex("""_sysconfigdata_[a-z]*_android_aarch64-linux-android\.py"""))
    } != false) {
    throw GradleException("The CPython prefix is not built for aarch64-linux-android.")
}
if (!pythonEntry.isFile || pythonEntry.extension != "py") {
    throw GradleException("The Python entry script must be an existing .py file.")
}
val applicationIdValue = providers.gradleProperty("applicationId").orElse("org.pypack.android").get()
val generatedAssets = layout.buildDirectory.dir("generated/pythonAssets")
val generatedJniLibs = layout.buildDirectory.dir("generated/pythonJniLibs")

android {
    namespace = "org.pypack.android"
    compileSdk = 35
    ndkVersion = "27.3.13750724"

    defaultConfig {
        applicationId = applicationIdValue
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        ndk {
            abiFilters += "arm64-v8a"
        }
        externalNativeBuild {
            cmake {
                arguments += listOf(
                    "-DPYTHON_PREFIX_DIR=${pythonPrefix.path}",
                    "-DPYTHON_VERSION=$pythonVersion",
                )
            }
        }
    }

    sourceSets.getByName("main").assets.srcDir(generatedAssets)
    sourceSets.getByName("main").jniLibs.srcDir(generatedJniLibs)

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    signingConfigs {
        create("release") {
            storeFile = providers.environmentVariable("ANDROID_SIGNING_KEYSTORE")
                .orNull?.let { file(it) }
            storePassword = providers.environmentVariable("ANDROID_SIGNING_STORE_PASSWORD").orNull
            keyAlias = providers.environmentVariable("ANDROID_SIGNING_KEY_ALIAS").orNull
            keyPassword = providers.environmentVariable("ANDROID_SIGNING_KEY_PASSWORD").orNull
        }
    }

    buildTypes {
        getByName("release") {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}

val stagePythonRuntime = tasks.register<Sync>("stagePythonRuntime") {
    into(generatedAssets)
    from(pythonRuntimeDir) {
        into("python/lib/python$pythonVersion")
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    from(pythonEntry) {
        into("python")
        rename { "app.py" }
    }
}

val stagePythonLibraries = tasks.register<Sync>("stagePythonLibraries") {
    into(generatedJniLibs)
    from(pythonLibDir) {
        include("*.so")
        into("arm64-v8a")
    }
}

tasks.named("preBuild").configure {
    dependsOn(stagePythonRuntime, stagePythonLibraries)
}

tasks.configureEach {
    if (name == "assembleRelease") {
        doFirst {
            val requiredSigningVariables = listOf(
                "ANDROID_SIGNING_KEYSTORE",
                "ANDROID_SIGNING_KEY_ALIAS",
                "ANDROID_SIGNING_STORE_PASSWORD",
                "ANDROID_SIGNING_KEY_PASSWORD",
            )
            val missing = requiredSigningVariables.filter {
                providers.environmentVariable(it).orNull.isNullOrBlank()
            }
            if (missing.isNotEmpty()) {
                throw GradleException(
                    "Release APK signing requires environment variables: ${missing.joinToString()}"
                )
            }
            val keystore = file(providers.environmentVariable("ANDROID_SIGNING_KEYSTORE").get())
            if (!keystore.isFile) {
                throw GradleException("Android signing keystore does not exist: $keystore")
            }
        }
    }
}
