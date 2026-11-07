import json
import subprocess
import unittest
from pathlib import Path
from xml.etree import ElementTree

import requirements


class IOSArm64TargetTests(unittest.TestCase):
    def test_windows_host_is_blocked_without_invoking_apple_tools(self):
        def unexpected_tool_lookup(_name):
            self.fail("Apple tools must not be queried on Windows")

        checks = requirements.check_requirements(
            host_platform="win32",
            find_executable=unexpected_tool_lookup,
        )

        self.assertEqual(checks[0].status, "blocked")
        self.assertIn("cannot produce a valid signed iOS app", checks[0].detail)
        self.assertTrue(any("iphoneos SDK" in check.name for check in checks))
        self.assertEqual(
            next(check for check in checks if check.name == "Device signing").status,
            "action",
        )

    def test_device_and_simulator_use_distinct_sdks(self):
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="/sdk/path\n", stderr="")

        def fake_find(name):
            return f"/tools/{name}"

        for target in ("device", "simulator"):
            checks = requirements.check_requirements(
                target,
                host_platform="darwin",
                find_executable=fake_find,
                run=fake_run,
            )
            self.assertEqual(checks[0].status, "pass")
            self.assertEqual(checks[2].status, "pass")

        self.assertEqual(
            [command[2] for command in commands],
            ["iphoneos", "iphonesimulator"],
        )

    def test_missing_device_sdk_is_reported_as_blocked(self):
        def fake_run(command, **_kwargs):
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="SDK cannot be located"
            )

        checks = requirements.check_requirements(
            host_platform="darwin",
            find_executable=lambda name: f"/tools/{name}",
            run=fake_run,
        )

        sdk_check = next(check for check in checks if check.name == "iphoneos SDK")
        self.assertEqual(sdk_check.status, "blocked")
        self.assertEqual(sdk_check.detail, "SDK cannot be located")

    def test_descriptor_documents_architectures_and_ipa_semantics(self):
        descriptor_path = Path(__file__).with_name("target.json")
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))

        self.assertEqual(descriptor["device_architectures"], ["arm64"])
        self.assertEqual(
            set(descriptor["simulator_architectures"]), {"arm64", "x86_64"}
        )
        self.assertTrue(descriptor["simulator_is_separate_build_target"])
        self.assertFalse(descriptor["artifact"]["is_generic_executable"])
        self.assertEqual(
            descriptor["implementation_status"],
            "descriptor_preflight_and_xcode_project_scaffold",
        )

    def test_xcode_project_scaffold_has_device_target_and_signing_settings(self):
        project_path = Path(__file__).with_name("PythonApp.xcodeproj") / "project.pbxproj"
        project = project_path.read_text(encoding="utf-8")

        self.assertIn("SUPPORTED_PLATFORMS = iphoneos;", project)
        self.assertIn("Python.xcframework in Embed Frameworks", project)
        self.assertIn("xcode_scaffold/process_python.sh", project)
        self.assertIn("ENABLE_USER_SCRIPT_SANDBOXING = NO;", project)
        self.assertIn("CODE_SIGN_STYLE = Automatic;", project)
        self.assertIn("DEVELOPMENT_TEAM = \"\";", project)
        scheme_path = (
            project_path.parent
            / "xcshareddata"
            / "xcschemes"
            / "PythonApp.xcscheme"
        )
        scheme = ElementTree.parse(scheme_path).getroot()
        self.assertIsNotNone(scheme.find("ArchiveAction"))


if __name__ == "__main__":
    unittest.main()
