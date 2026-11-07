import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

import build_recipe


class IOSBuildRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.framework = self.root / "Python.xcframework"
        self.framework.mkdir()
        self.app_source = self.root / "app"
        self.app_source.mkdir()
        (self.app_source / "main.py").write_text("print('hello')\n", encoding="utf-8")
        self.profile = self.root / "app.mobileprovision"
        self.profile.write_bytes(b"placeholder; not decoded by the preflight")
        self.inputs = build_recipe.BuildInputs(
            python_xcframework=self.framework,
            app_source=self.app_source,
            bundle_id="com.example.demo",
            team_id="ABCDE12345",
            provisioning_profile=self.profile,
            signing_identity="Apple Development: Example",
        )

    def write_framework_metadata(self, platform="ios", architectures=("arm64",)):
        library_id = f"{platform}-arm64"
        framework_slice = self.framework / library_id / "Python.framework"
        framework_slice.mkdir(parents=True, exist_ok=True)
        (framework_slice / "Python").write_bytes(b"placeholder framework binary")
        metadata = {
            "AvailableLibraries": [
                {
                    "LibraryIdentifier": library_id,
                    "LibraryPath": "Python.framework",
                    "SupportedArchitectures": list(architectures),
                    "SupportedPlatform": platform,
                }
            ]
        }
        with (self.framework / "Info.plist").open("wb") as file:
            plistlib.dump(metadata, file)

    def test_windows_fails_closed_without_querying_apple_tools(self):
        def unexpected_tool_lookup(_name):
            self.fail("Apple tool lookup must not run on Windows")

        checks = build_recipe.check_build_capability(
            self.inputs,
            host_platform="win32",
            find_executable=unexpected_tool_lookup,
        )

        self.assertEqual(checks[0].status, "blocked")
        self.assertTrue(any(check.status == "blocked" for check in checks))

    def test_device_slice_and_full_xcode_sdk_pass_preflight(self):
        self.write_framework_metadata()
        developer_dir = self.root / "Xcode.app" / "Contents" / "Developer"
        sdk_dir = (
            developer_dir
            / "Platforms"
            / "iPhoneOS.platform"
            / "Developer"
            / "SDKs"
            / "iPhoneOS.sdk"
        )
        sdk_dir.mkdir(parents=True)

        def fake_run(command, **_kwargs):
            if command[0] == "/tools/xcode-select":
                return subprocess.CompletedProcess(
                    command, 0, stdout=str(developer_dir), stderr=""
                )
            return subprocess.CompletedProcess(
                command, 0, stdout=str(sdk_dir), stderr=""
            )

        checks = build_recipe.check_build_capability(
            self.inputs,
            host_platform="darwin",
            find_executable=lambda name: f"/tools/{name}",
            run=fake_run,
        )

        self.assertFalse([check for check in checks if check.status == "blocked"])
        self.assertTrue(any(check.status == "action" for check in checks))
        report = build_recipe.format_report(checks)
        self.assertIn("no .app or .ipa was produced", report)

    def test_simulator_only_framework_is_rejected_for_device(self):
        self.write_framework_metadata(platform="ios-simulator")
        check = build_recipe._check_xcframework(self.framework)

        self.assertEqual(check.status, "blocked")
        self.assertIn("simulator-only", check.detail)

    def test_metadata_without_device_framework_binary_is_rejected(self):
        self.write_framework_metadata()
        framework_slice = self.framework / "ios-arm64" / "Python.framework"
        (framework_slice / "Python").unlink()

        check = build_recipe._check_xcframework(self.framework)

        self.assertEqual(check.status, "blocked")

    def test_missing_manual_signing_input_fails_closed(self):
        self.write_framework_metadata()
        inputs = build_recipe.BuildInputs(
            python_xcframework=self.framework,
            app_source=self.app_source,
            bundle_id="com.example.demo",
            team_id="ABCDE12345",
            provisioning_profile=self.root / "missing.mobileprovision",
            signing_identity="",
        )

        checks = build_recipe.check_build_capability(
            inputs,
            host_platform="win32",
            find_executable=lambda _name: self.fail("Must not query Apple tools"),
        )
        blocked = {check.name for check in checks if check.status == "blocked"}

        self.assertIn("Provisioning profile", blocked)
        self.assertIn("Signing identity", blocked)

    def test_missing_python_entry_point_is_rejected(self):
        (self.app_source / "main.py").unlink()
        check = build_recipe._check_app_source(self.app_source)

        self.assertEqual(check.status, "blocked")


if __name__ == "__main__":
    unittest.main()
