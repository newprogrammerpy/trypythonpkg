import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_config


class TargetProbeTests(unittest.TestCase):
    def test_support_requires_arm64_python_and_verified_executable(self):
        arm64_compiler = build_config.Path("vcvarsall.bat")
        arm64_python = build_config.Path("python.exe")
        with (
            patch.object(build_config.os, "name", "nt"),
            patch.object(build_config, "_compiler_candidates", return_value=[arm64_compiler]),
            patch.object(
                build_config,
                "_find_cpython_arm64",
                return_value=(arm64_python, "ARM64 (0xAA64)", True, ""),
            ),
            patch.object(
                build_config,
                "_try_compile",
                return_value=(True, "ARM64 (0xAA64)", "ARM64 (0xAA64)", "probe passed"),
            ) as try_compile,
        ):
            status = build_config.inspect_target()

        self.assertTrue(status.supported)
        self.assertTrue(status.executable_built)
        self.assertEqual(status.object_machine, "ARM64 (0xAA64)")
        self.assertEqual(status.executable_machine, "ARM64 (0xAA64)")
        self.assertEqual(status.python_executable, str(arm64_python))
        try_compile.assert_called_once_with(arm64_compiler)

    def test_wrong_object_machine_is_not_supported(self):
        compiler = build_config.Path("vcvarsall.bat")
        python = build_config.Path("python.exe")
        with (
            patch.object(build_config.os, "name", "nt"),
            patch.object(build_config, "_compiler_candidates", return_value=[compiler]),
            patch.object(
                build_config,
                "_find_cpython_arm64",
                return_value=(python, "ARM64 (0xAA64)", True, ""),
            ),
            patch.object(
                build_config,
                "_try_compile",
                return_value=(
                    False,
                    "0x8664",
                    None,
                    "Compiler emitted COFF machine 0x8664, not ARM64 (0xAA64).",
                ),
            ),
        ):
            status = build_config.inspect_target()

        self.assertFalse(status.supported)
        self.assertFalse(status.executable_built)
        self.assertIn("0x8664", status.reason)
        self.assertEqual(status.object_machine, "0x8664")
        self.assertIn(build_config.ARM64_COMPONENT, status.reason)

    def test_missing_toolchain_reports_unsupported(self):
        with (
            patch.object(build_config.os, "name", "nt"),
            patch.object(build_config, "_compiler_candidates", return_value=[]),
            patch.object(
                build_config,
                "_find_cpython_arm64",
                return_value=(None, None, False, "No ARM64 Python."),
            ),
            patch.object(build_config, "_try_compile") as try_compile,
        ):
            status = build_config.inspect_target()

        self.assertFalse(status.supported)
        self.assertIn("No MSVC ARM64 compiler", status.reason)
        self.assertIn(build_config.ARM64_COMPONENT, status.reason)
        self.assertIn("No ARM64 Python", status.reason)
        self.assertFalse(status.executable_built)
        try_compile.assert_not_called()

    def test_missing_arm64_python_skips_executable_build(self):
        compiler = build_config.Path("vcvarsall.bat")
        python = build_config.Path("python.exe")
        with (
            patch.object(build_config.os, "name", "nt"),
            patch.object(build_config, "_compiler_candidates", return_value=[compiler]),
            patch.object(
                build_config,
                "_find_cpython_arm64",
                return_value=(
                    python,
                    "AMD64 (0x8664)",
                    False,
                    "No CPython ARM64 runtime.",
                ),
            ),
            patch.object(build_config, "_try_compile") as try_compile,
        ):
            status = build_config.inspect_target()

        self.assertFalse(status.supported)
        self.assertFalse(status.executable_built)
        self.assertFalse(status.python_runtime_found)
        self.assertEqual(status.python_executable, str(python))
        self.assertEqual(status.python_machine, "AMD64 (0x8664)")
        self.assertIn("No CPython ARM64 runtime", status.reason)
        try_compile.assert_not_called()

    def test_failed_msvc_environment_initialization_has_install_guidance(self):
        compiler = build_config.Path("vcvarsall.bat")
        python = build_config.Path("python.exe")
        with (
            patch.object(build_config.os, "name", "nt"),
            patch.object(build_config, "_compiler_candidates", return_value=[compiler]),
            patch.object(
                build_config,
                "_find_cpython_arm64",
                return_value=(python, "ARM64 (0xAA64)", True, ""),
            ),
            patch.object(
                build_config,
                "_try_compile",
                return_value=(
                    False,
                    None,
                    None,
                    "MSVC could not initialize its x64-to-ARM64 environment. "
                    + build_config.INSTALL_GUIDANCE,
                ),
            ),
        ):
            status = build_config.inspect_target()

        self.assertFalse(status.supported)
        self.assertIn("Individual components", status.reason)

    def test_pe_machine_reads_machine_field_from_pe_header(self):
        image = bytearray(70)
        image[:2] = b"MZ"
        image[0x3C:0x40] = (64).to_bytes(4, "little")
        image[64:68] = b"PE\0\0"
        image[68:70] = build_config.ARM64_COFF_MACHINE.to_bytes(2, "little")
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "python.exe"
            executable.write_bytes(image)
            self.assertEqual(
                build_config._pe_machine(executable), build_config.ARM64_COFF_MACHINE
            )

    def test_arm64_python_requires_matching_runtime_dll(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "python.exe"
            runtime = Path(temporary) / "python314.dll"
            executable.touch()
            runtime.touch()
            with (
                patch.object(build_config, "_python_candidates", return_value=[executable]),
                patch.object(
                    build_config,
                    "_pe_machine",
                    side_effect=lambda path: (
                        build_config.ARM64_COFF_MACHINE
                        if path == executable
                        else 0x8664
                    ),
                ),
            ):
                found, machine, runtime_found, reason = build_config._find_cpython_arm64()

        self.assertEqual(found, executable)
        self.assertEqual(machine, "ARM64 (0xAA64)")
        self.assertFalse(runtime_found)
        self.assertIn("no ARM64 CPython DLL", reason)

    def test_arm64_python_runtime_requires_arm64_executable_and_dll(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "python.exe"
            runtime = Path(temporary) / "python314.dll"
            executable.touch()
            runtime.touch()
            with (
                patch.object(build_config, "_python_candidates", return_value=[executable]),
                patch.object(
                    build_config,
                    "_pe_machine",
                    return_value=build_config.ARM64_COFF_MACHINE,
                ),
            ):
                found, machine, runtime_found, reason = build_config._find_cpython_arm64()

        self.assertEqual(found, executable)
        self.assertEqual(machine, "ARM64 (0xAA64)")
        self.assertTrue(runtime_found)
        self.assertEqual(reason, "")

    def test_coff_machine_is_read_from_object_header(self):
        self.assertEqual(build_config._coff_machine(b"\x64\x86"), 0x8664)
        self.assertEqual(
            build_config._coff_machine(b"\x64\xAA"),
            build_config.ARM64_COFF_MACHINE,
        )

    def test_coff_machine_rejects_truncated_object(self):
        with self.assertRaisesRegex(ValueError, "empty COFF object"):
            build_config._coff_machine(b"\xAA")


if __name__ == "__main__":
    unittest.main()
