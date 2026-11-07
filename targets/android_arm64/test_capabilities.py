import os
import tempfile
import unittest
import subprocess
from pathlib import Path

from targets.android_arm64.build import (
    AndroidBuildError,
    _command_for_executable,
    _python_prefix_status,
    build_apk,
    inspect_requirements,
)
from targets.android_arm64.capabilities import TARGET, get_build_decision


class AndroidArm64CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_python_prefix(self):
        prefix = self.root / "python-prefix"
        version = "3.14"
        runtime = prefix / "lib" / f"python{version}"
        runtime.mkdir(parents=True)
        (prefix / "include" / f"python{version}").mkdir(parents=True)
        (prefix / "include" / f"python{version}" / "Python.h").write_text(
            "/* test header */", encoding="ascii"
        )
        (runtime / "_sysconfigdata__android_aarch64-linux-android.py").write_text(
            "# target metadata", encoding="ascii"
        )
        (runtime / "encodings").mkdir()
        (runtime / "encodings" / "__init__.py").write_text(
            "# CPython bootstrap package", encoding="ascii"
        )
        (runtime / "os.py").write_text("# stdlib module", encoding="ascii")
        (prefix / "lib" / f"libpython{version}.so").write_bytes(b"python")
        (prefix / "lib" / "libcrypto_python.so").write_bytes(b"crypto")
        return prefix

    def make_android_sdk(self):
        sdk = self.root / "sdk"
        (sdk / "platforms" / "android-35").mkdir(parents=True)
        ndk = sdk / "ndk" / "27.3.13750724"
        ndk.mkdir(parents=True)
        (ndk / "package.xml").write_text("<package/>", encoding="ascii")
        cmake = sdk / "cmake" / "3.22.1" / "bin"
        cmake.mkdir(parents=True)
        executable = "cmake.exe" if os.name == "nt" else "cmake"
        (cmake / executable).write_bytes(b"")
        return sdk

    @staticmethod
    def fake_which(name):
        return f"C:\\tools\\{name}.exe"

    @staticmethod
    def version_runner(args, **_kwargs):
        if args[1] == "-version":
            return subprocess.CompletedProcess(args, 0, "", 'openjdk version "17.0.10"')
        return subprocess.CompletedProcess(args, 0, "Gradle 8.11.1", "")

    def test_target_is_android_arm64_apk_not_a_generic_executable(self):
        self.assertEqual(TARGET["platform"], "Android")
        self.assertEqual(TARGET["abi"], "arm64-v8a")
        self.assertEqual(TARGET["artifact"], "APK")
        self.assertFalse(TARGET["artifact_is_generic_executable"])
        self.assertFalse(TARGET["supports_windows_exe_conversion"])
        self.assertTrue(TARGET["python_apk_backend_implemented"])

    def test_missing_requirements_fail_closed(self):
        decision = get_build_decision({}, which=lambda _name: None)

        self.assertFalse(decision["can_build_apk"])
        self.assertTrue(decision["reasons"])
        self.assertFalse(decision["requirements"]["android_sdk"]["detected"])
        self.assertFalse(decision["requirements"]["android_ndk"]["detected"])
        self.assertFalse(decision["requirements"]["cpython_android_prefix"]["detected"])

    def test_detects_compatible_sdk_toolchain_and_android_python_prefix(self):
        sdk = self.make_android_sdk()
        prefix = self.make_python_prefix()
        requirements = inspect_requirements(
            prefix,
            environment={"ANDROID_HOME": str(sdk)},
            which=self.fake_which,
            runner=self.version_runner,
        )

        self.assertTrue(all(item["detected"] for item in requirements.values()))

    def test_prefix_rejects_non_android_arm64_runtime(self):
        prefix = self.make_python_prefix()
        runtime = prefix / "lib" / "python3.14"
        metadata = runtime / "_sysconfigdata__android_aarch64-linux-android.py"
        metadata.rename(runtime / "_sysconfigdata__android_x86_64-linux-android.py")

        detected, detail = _python_prefix_status(prefix)

        self.assertFalse(detected)
        self.assertIn("aarch64-linux-android", detail)

    def test_prefix_rejects_runtime_without_bootstrap_stdlib(self):
        prefix = self.make_python_prefix()
        (prefix / "lib" / "python3.14" / "encodings" / "__init__.py").unlink()

        detected, detail = _python_prefix_status(prefix)

        self.assertFalse(detected)
        self.assertIn("encodings", detail)

    def test_windows_gradle_batch_files_run_through_command_processor(self):
        command = _command_for_executable(
            r"C:\Program Files\Gradle\gradle.bat",
            ["--version"],
            windows=True,
            command_processor=r"C:\Windows\System32\cmd.exe",
        )

        self.assertEqual(
            command,
            [
                r"C:\Windows\System32\cmd.exe",
                "/d",
                "/c",
                r"C:\Program Files\Gradle\gradle.bat",
                "--version",
            ],
        )

    @unittest.skipUnless(os.name == "nt", "Windows batch execution")
    def test_windows_gradle_batch_command_is_executable(self):
        executable = self.root / "gradle test.cmd"
        executable.write_text("@echo off\r\necho Gradle 8.11.1\r\n", encoding="ascii")

        result = subprocess.run(
            _command_for_executable(str(executable), ["--version"]),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Gradle 8.11.1", result.stdout)

    def test_release_build_requires_keystore_and_all_signing_credentials(self):
        sdk = self.make_android_sdk()
        prefix = self.make_python_prefix()
        keystore = self.root / "release.keystore"
        keystore.touch()
        environment = {
            "ANDROID_HOME": str(sdk),
            "ANDROID_SIGNING_KEYSTORE": str(keystore),
            "ANDROID_SIGNING_KEY_ALIAS": "release",
            "ANDROID_SIGNING_STORE_PASSWORD": "store-password",
            "ANDROID_SIGNING_KEY_PASSWORD": "key-password",
        }

        requirements = inspect_requirements(
            prefix,
            release=True,
            environment=environment,
            which=self.fake_which,
            runner=self.version_runner,
        )

        self.assertTrue(requirements["release_signing"]["detected"])
        del environment["ANDROID_SIGNING_KEY_PASSWORD"]
        requirements = inspect_requirements(
            prefix,
            release=True,
            environment=environment,
            which=self.fake_which,
            runner=self.version_runner,
        )
        self.assertFalse(requirements["release_signing"]["detected"])

    def test_build_stops_before_gradle_when_prerequisites_are_missing(self):
        source = self.root / "main.py"
        source.write_text("print('hello')", encoding="utf-8")
        commands = []

        def runner(args, **kwargs):
            commands.append(args)
            return self.version_runner(args, **kwargs)

        with self.assertRaisesRegex(AndroidBuildError, "Android APK build is blocked"):
            build_apk(
                source,
                self.root / "app.apk",
                self.make_python_prefix(),
                environment={},
                which=self.fake_which,
                runner=runner,
            )

        self.assertFalse(any("assembleDebug" in command for command in commands))

    def test_cli_check_reports_missing_prerequisites_without_building(self):
        from unittest.mock import patch

        from targets.android_arm64 import build

        output = []
        with (
            patch("sys.argv", ["build.py", "--check", "--python-prefix", str(self.root / "missing")]),
            patch.dict(os.environ, {"PATH": ""}, clear=True),
            patch("builtins.print", side_effect=output.append),
        ):
            status = build.main()

        self.assertEqual(status, 1)
        self.assertTrue(any("android_sdk" in line and "MISSING" in line for line in output))
        self.assertTrue(any("cpython_android_prefix" in line and "MISSING" in line for line in output))

    def test_build_rejects_non_apk_output(self):
        source = self.root / "main.py"
        source.write_text("print('hello')", encoding="utf-8")

        with self.assertRaisesRegex(AndroidBuildError, "Output must use the .apk extension"):
            build_apk(
                source,
                self.root / "result.bin",
                self.make_python_prefix(),
                environment={},
                which=lambda _name: None,
            )

    def test_adapter_stages_debug_apk_only_after_readiness_checks_pass(self):
        sdk = self.make_android_sdk()
        prefix = self.make_python_prefix()
        source = self.root / "main.py"
        source.write_text("print('hello')", encoding="utf-8")
        output = self.root / "result.apk"
        build_commands = []

        def runner(args, **kwargs):
            if args[1] in {"-version", "--version"}:
                return self.version_runner(args, **kwargs)
            build_commands.append(args)
            project = Path(args[args.index("-p") + 1])
            artifact = project / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"test APK artifact")
            return subprocess.CompletedProcess(args, 0, "", "")

        build_apk(
            source,
            output,
            prefix,
            environment={"ANDROID_HOME": str(sdk)},
            which=self.fake_which,
            runner=runner,
        )

        self.assertEqual(output.read_bytes(), b"test APK artifact")
        self.assertEqual(len(build_commands), 1)
        self.assertIn("assembleDebug", build_commands[0])


if __name__ == "__main__":
    unittest.main()
