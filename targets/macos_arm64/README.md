# macOS ARM64 target

This target builds an **unsigned native `.app` application bundle** on an
Apple Silicon Mac. It compiles a Mach-O launcher with Xcode's `clang`, embeds
an arm64-capable CPython `Python.framework`, and copies the Python entry point
plus explicitly selected project files. It checks the macOS SDK, build tools,
framework layout, and framework Mach-O architecture before assembling the
bundle.

## Prerequisites and preflight

Run these steps on the Mac that will build the app; Windows and Intel Macs
cannot produce this native ARM64 artifact. No cross-compilation or signing is
performed by this target.

1. Install Xcode or Apple's command-line tools on that Mac if needed. The
   builder requires `xcrun`, the macOS SDK, Xcode `clang`, and `lipo`. Check
   that the selected developer tools and SDK are available:

   ```sh
   xcode-select -p
   xcrun --sdk macosx --show-sdk-path
   xcrun --find clang
   xcrun --find lipo
   ```

2. Provide a macOS ARM64 CPython framework. For example, install the
   python.org macOS universal2 or Apple Silicon distribution, then check that
   the framework contains a usable interpreter:

   ```sh
   test -x /Library/Frameworks/Python.framework/Versions/3.13/bin/python3
   ```

   Adjust `3.13` to the installed version. The builder selects the newest
   version with the expected framework layout and verifies its `arm64` slice.
   The framework is copied into the app; third-party Python packages are not
   installed or collected automatically.

3. From the project root, run the preflight report and target tests:

   ```sh
   python3 targets/macos_arm64/capability.py
   python3 -m unittest discover -s targets/macos_arm64 -p 'test_*.py'
   ```

   `host_ready: true` means the host, SDK, and native tools are ready; it does
   not verify a specific Python framework, signing identity, or app dependencies.

## Build and verify

From the project root, use the actual entry point, a unique reverse-DNS bundle
ID, and the framework path/version available on that Mac:

```sh
python3 targets/macos_arm64/build_app.py src/main.py dist/MyApp.app \
  --python-framework /Library/Frameworks/Python.framework \
  --bundle-id com.example.MyApp \
  --include src/package
```

Add a repeatable `--include` for each application module or project-local
dependency that the entry point imports. Only files within the entry point's
directory are accepted. Python extension modules included in the app must have
an `arm64` slice and their non-system dependencies must also be available on
the Mac. The output must not already exist and must be outside the entry
point's directory.

Check the resulting bundle and launcher architecture:

```sh
plutil -lint dist/MyApp.app/Contents/Info.plist
file dist/MyApp.app/Contents/MacOS/MyApp
xcrun lipo -verify_arch arm64 dist/MyApp.app/Contents/MacOS/MyApp
open dist/MyApp.app
```

`open` is a basic launch check, not an application test; exercise the app's
actual features and dependencies on the target macOS version as well.

## Packaging, signing, and notarization

An `.app` is an application bundle, **not an installer**. A `.dmg` disk image
or `.pkg` installer is a separate packaging step; creating either does not
automatically sign or notarize its contents. This builder produces an unsigned
app and does not create `.dmg`/`.pkg` files, sign nested code, check Developer
ID credentials, or submit anything to Apple.

For normal external distribution, use a macOS release pipeline with the
appropriate Developer ID identity to sign the app and its embedded code,
typically with hardened runtime and a timestamp. Then package the signed app
as the intended deliverable (`.app`, `.dmg`, or `.pkg`) and submit that
deliverable for Apple notarization using `notarytool`; staple the ticket where
supported. Keep signing credentials and notarization keychain profiles on the
Mac release host, not in this project. Follow Apple's current distribution
requirements for the chosen format.

Official references:

- [Apple: Placing content in a bundle](https://developer.apple.com/documentation/bundleresources/placing-content-in-a-bundle) — `Info.plist`, Mach-O executable, resources, and frameworks locations.
- [Apple: Installing the command-line tools](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools) — Xcode command-line tool installation.
- [Python: Using Python on macOS](https://docs.python.org/3/using/mac.html) — python.org macOS installers and `Python.framework`.
- [Python: Embedding Python](https://docs.python.org/3/extending/embedding.html) — CPython embedding concepts.
- [Apple: Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution) — Developer ID, hardened runtime, notarization, and supported deliverables including apps, disk images, and installer packages.

Run the platform-independent target tests on Windows or macOS from the project
root with `python -m unittest discover -s targets\macos_arm64 -p test_*.py`.
