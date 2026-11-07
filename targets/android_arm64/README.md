# Android ARM64 Python APK target

This target provides a small native Android app scaffold and a Python adapter
for packaging one `.py` entry script as an `arm64-v8a` APK. It embeds a
prebuilt Android CPython runtime using the CPython embedding API via JNI, stages
the standard library and script as app assets, and links the prefix's shared
CPython libraries into the APK. It does not convert Windows desktop `.exe`
files, and an APK is an Android app package, not a generic executable.

## Requirements

- Android SDK platform `android-35`
- Android NDK `27.3.13750724` and Android SDK CMake `3.22.1`
- JDK 17 or newer and Gradle 8.11.1 or newer on `PATH`
- A CPython Android prefix built for `aarch64-linux-android`. It must contain
  `include/pythonX.Y/Python.h`, `lib/libpythonX.Y.so`, the matching
  `lib/pythonX.Y` runtime and Android arm64 sysconfig metadata, plus CPython's
  `lib*_python.so` dependency libraries.

The adapter checks these prerequisites and stops before Gradle if any required
item is missing or incompatible. It never invokes `sdkmanager` or installs
tools. The Android Gradle Plugin and other Gradle dependencies may be resolved
by Gradle when a build is explicitly run.

Check the local prerequisites before providing an entry script:

```powershell
python -m targets.android_arm64.build --check `
  --python-prefix C:\path\to\cpython-android\prefix
```

The check lists each missing component and exits nonzero while any required
tool, SDK component, or compatible CPython runtime is unavailable. It does not
install components or accept SDK licenses.

CPython's Android build workflow requires Linux or macOS. For example, from a
CPython source checkout, its documented Android tooling can build and package
the `aarch64-linux-android` prefix. On Windows, use an already-built Android
prefix rather than trying to build CPython there.

## Build

```powershell
python -m targets.android_arm64.build .\main.py .\main.apk `
  --python-prefix C:\path\to\cpython-android\prefix
```

The default build creates a debug APK signed with the Android SDK's debug key.
For a release APK, provide an existing signing keystore and its credentials
through environment variables, then add `--release`:

```powershell
$env:ANDROID_SIGNING_KEYSTORE = 'C:\keys\release.jks'
$env:ANDROID_SIGNING_KEY_ALIAS = 'release'
$env:ANDROID_SIGNING_STORE_PASSWORD = '...'
$env:ANDROID_SIGNING_KEY_PASSWORD = '...'
python -m targets.android_arm64.build .\main.py .\main-release.apk `
  --python-prefix C:\path\to\cpython-android\prefix `
  --application-id org.example.myapp `
  --release
```

Signing secrets are read from the environment, not written to the generated
project. The script runs Gradle in a temporary project and copies the resulting
APK to the requested output path only after a successful build.

## Scope and limits

The scaffold currently packages one Python entry script and the CPython runtime
for `arm64-v8a`. It does not resolve third-party Python dependencies, compile
arbitrary extension modules, package user-provided resource trees, or provide a
desktop-style Python console. A successful prerequisite check is not proof of
runtime compatibility; the actual Android build and device execution still
need to be tested. No APK was built as part of adding this scaffold.

## References

- [Python on Android](https://docs.python.org/3/using/android.html)
- [CPython Android build and packaging instructions](https://github.com/python/cpython/blob/3.15/Platforms/Android/README.md)
- [CPython Android testbed](https://github.com/python/cpython/tree/3.15/Platforms/Android/testbed)
- [Android command-line builds](https://developer.android.com/build/building-cmdline)
- [Android app signing](https://developer.android.com/studio/publish/app-signing)
