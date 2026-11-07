# iOS ARM64 target

This target is for a **physical iPhone/iPad** (`arm64`), not an iOS simulator.
The checked-in `PythonApp.xcodeproj` is an Xcode-side starter project that embeds
an iOS-built CPython runtime using Python-Apple-support. It invokes the bundled
`app/main.py`; it is a bootstrap example, not a Python-to-native-UI converter.

## Host and prerequisites

Building and signing a device app requires macOS with a full Xcode installation
and the iPhoneOS SDK. Windows cannot run Xcode or produce a valid signed iOS
device app. The read-only checks do not install tools, accept licenses, invoke a
build, sign an app, or modify the host.

If building on a VM, it must be a **legitimate macOS VM on Apple-supported
hardware**, with a full Xcode installation obtained through Apple and its
current license terms satisfied. A Windows or Linux VM (including WSL) is not
a substitute. The Mac must be reachable as a configured remote build host; this
folder session cannot run Xcode locally. Do not use third-party iOS SDK or
Xcode downloads.

From the repository root, run the prerequisite check:

```powershell
python targets\ios_arm64\requirements.py
```

On macOS, the equivalent command is:

```sh
python3 targets/ios_arm64/requirements.py
```

Before building, provide these inputs in `targets/ios_arm64/`, beside
`PythonApp.xcodeproj`:

- `Python.xcframework` from a compatible Python-Apple-support release. It must
  contain the **iOS device `arm64`** slice; a simulator-only framework is not
  suitable.
- An `app/` directory containing the Python entry point `main.py`.
- An app-specific bundle identifier and an Apple Developer team.
- For manual signing, a matching provisioning profile and an available signing
  identity. Alternatively, configure Xcode-managed signing with an Apple
  account/team that can provision this app.

A regular Windows CPython installation is not an iOS runtime. See
[Python-Apple-support](https://github.com/beeware/Python-Apple-support) or
[BeeWare Briefcase](https://briefcase.beeware.org/) for runtime/project
integration options. The project build phase calls the supplied
`Python.xcframework/build/build_utils.sh` `install_python` helper to embed the
runtime and app source.

## Open, configure, and archive

Open `targets/ios_arm64/PythonApp.xcodeproj` in Xcode. Select the **PythonApp**
target and set its bundle identifier and **Signing & Capabilities** team. For
automatic signing, enable *Automatically manage signing* and confirm the team
can create the required profile. For manual signing, select the matching
provisioning profile and signing identity. The project template uses automatic
signing and a placeholder bundle ID; neither setting is sufficient to sign a
real app unchanged.

The project links and embeds `Python.xcframework`, copies `app/` into the app
bundle, and runs `xcode_scaffold/process_python.sh` before embedding frameworks.
Its build settings already disable user-script sandboxing for the runtime
integration. To archive from macOS after supplying the inputs and configuring
signing:

```sh
xcodebuild \
  -project targets/ios_arm64/PythonApp.xcodeproj \
  -scheme PythonApp \
  -destination 'generic/platform=iOS' \
  -archivePath build/PythonApp.xcarchive \
  archive
```

The `build_recipe.py` preflight can separately check for the device runtime,
source directory, bundle/team identifiers, signing inputs, full Xcode selection,
and iPhoneOS SDK. For manual signing:

```sh
python3 targets/ios_arm64/build_recipe.py \
  --python-xcframework targets/ios_arm64/Python.xcframework \
  --app-source targets/ios_arm64/app \
  --bundle-id com.example.myapp \
  --team-id ABCDE12345 \
  --provisioning-profile /path/to/app.mobileprovision \
  --signing-identity "Apple Development: Developer Name"
```

For Xcode-managed signing, replace the last two arguments with
`--xcode-managed-signing`. The preflight does not decode profiles, inspect
keychains, prove team/profile compatibility, or certify distribution
eligibility. Xcode must still validate signing and successfully archive.

An `.xcarchive` is Xcode's archive bundle. An exported `.ipa` is a ZIP archive
containing the signed iOS app bundle (typically under `Payload/`), **not a
generic executable**. Use Xcode Organizer's **Distribute App** flow on the
archive to export an IPA for the intended distribution method; App Store,
ad hoc, and other routes have separate eligibility and signing requirements.
Neither preflight nor this scaffold creates an `.app`, `.xcarchive`, or `.ipa`.
The target is not wired into `pypack.py`.

Apple App Review Guideline
[2.5.2](https://developer.apple.com/app-store/review/guidelines/#software-requirements)
restricts downloading, installing, or executing code that introduces or changes
app features; assess any dynamic-code behavior against current Apple guidance.

Official references:

- [CPython: Using Python on iOS](https://docs.python.org/3/using/ios.html)
- [PEP 730: Adding iOS as a supported platform](https://peps.python.org/pep-0730/)
- [Python-Apple-support usage guide](https://github.com/beeware/Python-Apple-support/blob/main/USAGE.md)
- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
