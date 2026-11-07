import unittest
import tempfile
from pathlib import Path
from unittest import mock

import build_app
from capability import get_capability


class MacOSArm64CapabilityTests(unittest.TestCase):
    def test_windows_host_is_not_reported_as_build_capable(self):
        result = get_capability(system="Windows", machine="AMD64")

        self.assertFalse(result["host_ready"])
        self.assertTrue(result["builder_available"])
        self.assertFalse(result["macos_sdk"]["checked"])
        self.assertTrue(any("macOS build host" in item for item in result["blockers"]))

    def test_macos_sdk_probe_requires_tools_and_python_framework_argument(self):
        with tempfile.TemporaryDirectory() as sdk_path:
            result = get_capability(
                system="Darwin",
                machine="arm64",
                sdk_path=sdk_path,
                native_tools={"clang": "/usr/bin/clang", "lipo": "/usr/bin/lipo"},
            )

        self.assertTrue(result["host_ready"])
        self.assertTrue(result["macos_sdk"]["checked"])
        self.assertTrue(result["macos_sdk"]["available"])
        self.assertTrue(result["builder_available"])
        self.assertTrue(any("Python.framework" in item for item in result["blockers"]))
        self.assertEqual(result["signing"]["status"], "not_checked")

    def test_macos_without_sdk_reports_the_sdk_blocker(self):
        result = get_capability(
            system="Darwin",
            machine="x86_64",
            sdk_error="Xcode is not installed.",
            native_tools={"clang": "/usr/bin/clang", "lipo": "/usr/bin/lipo"},
        )

        self.assertFalse(result["host_ready"])
        self.assertTrue(result["macos_sdk"]["checked"])
        self.assertIn("Xcode is not installed.", result["blockers"])


class MacOSArm64BuilderTests(unittest.TestCase):
    def test_refuses_to_build_on_windows_before_touching_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "main.py"
            output = root / "dist" / "Example.app"
            source.write_text("print('hello')\n", encoding="utf-8")

            with mock.patch.object(build_app.sys, "platform", "win32"), \
                    mock.patch.object(build_app.platform, "system", return_value="Windows"):
                with self.assertRaisesRegex(build_app.BuildError, "requires macOS"):
                    build_app.build_app(
                        source, output, root / "Python.framework", "org.example.Test"
                    )

            self.assertFalse(output.exists())

    def test_finds_versioned_cpython_framework_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            framework = Path(temporary) / "Python.framework"
            version = framework / "Versions" / "3.13"
            (version / "bin").mkdir(parents=True)
            (version / "lib" / "python3.13").mkdir(parents=True)
            (version / "bin" / "python3").write_bytes(b"runtime")

            found_framework, found_version, interpreter = build_app._framework_version(
                framework
            )

            self.assertEqual(found_framework, framework.resolve())
            self.assertEqual(found_version, "3.13")
            self.assertEqual(interpreter, version / "bin" / "python3")

    def test_includes_cannot_escape_source_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = project / "main.py"
            source.write_text("", encoding="utf-8")
            outside = root / "secret.py"
            outside.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(build_app.BuildError, "inside the source"):
                build_app._safe_include_files(project, source, [outside])

    def test_native_launcher_executes_bundled_runtime_and_entry_point(self):
        launcher = build_app._launcher_source("3.13")

        self.assertIn("Python.framework/Versions/%s", launcher)
        self.assertIn('executable, "3.13"', launcher)
        self.assertIn("__main__.py", launcher)
        self.assertIn('setenv("PYTHONHOME"', launcher)
        self.assertIn('setenv("PYTHONPATH"', launcher)
        self.assertIn("execv(python_executable, python_argv)", launcher)


if __name__ == "__main__":
    unittest.main()
