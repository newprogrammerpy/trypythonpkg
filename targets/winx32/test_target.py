import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from targets.winx32 import target


def _write_x86_pe(path: Path) -> None:
    header = bytearray(64)
    header[:2] = b"MZ"
    header[0x3C:0x40] = (64).to_bytes(4, "little")
    path.write_bytes(header + b"PE\0\0\x4c\x01")


def _make_python_root(path: Path) -> Path:
    path.mkdir()
    _write_x86_pe(path / "python.exe")
    for name in ("python314.dll", "python3.dll", "vcruntime140.dll"):
        _write_x86_pe(path / name)
    (path / "LICENSE.txt").write_text("license", encoding="ascii")
    (path / "Lib" / "encodings").mkdir(parents=True)
    (path / "Lib" / "encodings" / "__init__.py").write_text("", encoding="ascii")
    (path / "DLLs").mkdir()
    return path


class Winx32CapabilityTests(unittest.TestCase):
    def test_explicit_vcvarsall_path_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            vcvarsall = Path(directory) / "vcvarsall.bat"
            vcvarsall.write_text("@echo off\n", encoding="ascii")
            with patch.dict(target.os.environ, {"WINX32_VCVARSALL": str(vcvarsall)}):
                found = target._find_vcvarsall()

        self.assertEqual(found, vcvarsall.resolve())

    def test_non_windows_platform_is_unavailable(self):
        result = target.probe(platform="linux")

        self.assertFalse(result.ready)
        self.assertIn("requires Windows", result.message)

    def test_missing_toolchain_is_reported(self):
        with patch.object(
            target, "_discover_toolchain", side_effect=target.TargetBuildError("MSVC unavailable")
        ):
            result = target.probe(platform="win32")

        self.assertFalse(result.ready)
        self.assertIn("MSVC unavailable", result.message)

    def test_invalid_explicit_vcvarsall_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-vcvarsall.bat"
            with patch.dict(target.os.environ, {"WINX32_VCVARSALL": str(missing)}):
                with self.assertRaisesRegex(target.TargetBuildError, "WINX32_VCVARSALL"):
                    target._discover_toolchain()

    def test_toolchain_without_x86_runtime_is_not_build_ready(self):
        with (
            patch.object(
                target,
                "_discover_toolchain",
                return_value=("cl.exe", "rc.exe", {"PATH": "tools"}),
            ),
            patch.object(
                target,
                "_find_python_root",
                return_value=(None, "No 32-bit CPython runtime was found."),
            ),
        ):
            result = target.probe(platform="win32")

        self.assertFalse(result.ready)
        self.assertEqual(result.compiler, "cl.exe")
        self.assertIn("32-bit CPython runtime", result.message)

    def test_probe_is_ready_only_with_valid_x86_runtime_and_toolchain(self):
        with tempfile.TemporaryDirectory() as directory:
            python_root = _make_python_root(Path(directory) / "python-x86")
            resolved_python_root = python_root.resolve()
            with patch.object(
                target,
                "_discover_toolchain",
                return_value=("cl.exe", "rc.exe", {"PATH": "tools"}),
            ):
                result = target.probe(platform="win32", python_root=python_root)

        self.assertTrue(result.ready)
        self.assertEqual(result.python_root, resolved_python_root)

    def test_python_root_rejects_x64_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            python_root = _make_python_root(Path(directory) / "python-x64")
            header = bytearray(64)
            header[:2] = b"MZ"
            header[0x3C:0x40] = (64).to_bytes(4, "little")
            (python_root / "python.exe").write_bytes(header + b"PE\0\0\x64\x86")

            error = target._validate_python_root(python_root)

        self.assertIn("32-bit x86 python.exe", error)

    def test_python_root_rejects_x64_extension_module(self):
        with tempfile.TemporaryDirectory() as directory:
            python_root = _make_python_root(Path(directory) / "python-x86")
            extension = python_root / "DLLs" / "sample.pyd"
            header = bytearray(64)
            header[:2] = b"MZ"
            header[0x3C:0x40] = (64).to_bytes(4, "little")
            extension.write_bytes(header + b"PE\0\0\x64\x86")

            error = target._validate_python_root(python_root)

        self.assertIn("non-x86", error)
        self.assertIn("sample.pyd", error)

    def test_python3_dll_alone_is_not_mistaken_for_versioned_runtime_dll(self):
        with tempfile.TemporaryDirectory() as directory:
            python_root = _make_python_root(Path(directory) / "python-x86")
            (python_root / "python314.dll").unlink()

            error = target._validate_python_root(python_root)

        self.assertIn("versioned python DLL", error)

    def test_x86_runtime_does_not_require_x64_only_vcruntime140_1(self):
        with tempfile.TemporaryDirectory() as directory:
            python_root = _make_python_root(Path(directory) / "python-x86")

            error = target._validate_python_root(python_root)

        self.assertIsNone(error)

    def test_x86_runtime_staging_uses_only_architecture_appropriate_vcruntime(self):
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_root = _make_python_root(root / "python-x86")
            stage = root / "stage"
            stage.mkdir()

            target._stage_x86_runtime(python_root, stage)

            self.assertTrue((stage / "vcruntime140.dll").is_file())
            self.assertFalse((stage / "vcruntime140_1.dll").exists())
            with zipfile.ZipFile(stage / "python314.zip") as library:
                self.assertIn("encodings/__init__.py", library.namelist())
            self.assertIn("import site", (stage / "python314._pth").read_text(encoding="ascii"))

    def test_environment_parser_preserves_values_containing_equals(self):
        parsed = target._parse_environment("PATH=C:\\Tools\nTOKEN=a=b=c\n")

        self.assertEqual(parsed["PATH"], "C:\\Tools")
        self.assertEqual(parsed["TOKEN"], "a=b=c")

    def test_x86_executable_check_reads_pe_machine_type(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "probe.exe"
            _write_x86_pe(executable)
            self.assertTrue(target._is_x86_executable(executable))

            header = bytearray(64)
            header[:2] = b"MZ"
            header[0x3C:0x40] = (64).to_bytes(4, "little")
            executable.write_bytes(header + b"PE\0\0\x64\x86")
            self.assertFalse(target._is_x86_executable(executable))

    def test_build_fails_closed_when_x86_runtime_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            source.write_text("print('hello')\n", encoding="utf-8")
            with (
                patch.object(target.sys, "platform", "win32"),
                patch.object(target, "_discover_toolchain") as discover_toolchain,
                self.assertRaisesRegex(target.TargetBuildError, "CPython root does not exist"),
            ):
                target.build_windows_exe(source, python_root=Path(directory) / "missing")

        discover_toolchain.assert_not_called()

    def test_build_refuses_to_overwrite_shared_packager(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "app.py"
            source.write_text("print('hello')\n", encoding="utf-8")
            packager = Path(target.__file__).resolve().parents[2] / "pypack.py"
            with (
                patch.object(target.sys, "platform", "win32"),
                self.assertRaisesRegex(target.TargetBuildError, "cannot overwrite"),
            ):
                target.build_windows_exe(source, packager, python_root=Path(directory))

    def test_build_produces_only_verified_x86_output(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_root = _make_python_root(root / "python-x86")
            source = root / "app.py"
            source.write_text("print('hello')\n", encoding="utf-8")
            output = root / "app.exe"

            def run_tool(command, **_kwargs):
                if command[0] == "rc.exe":
                    resource = Path(command[2][3:])
                    resource.write_bytes(b"resource")
                elif command[0] == "cl.exe":
                    executable = Path(next(arg[5:] for arg in command if arg.startswith("/OUT:")))
                    _write_x86_pe(executable)
                else:
                    self.fail(f"Unexpected command: {command}")
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                patch.object(target.sys, "platform", "win32"),
                patch.object(
                    target,
                    "_discover_toolchain",
                    return_value=("cl.exe", "rc.exe", {"PATH": "tools"}),
                ),
                patch.object(target.subprocess, "run", side_effect=run_tool),
            ):
                built = target.build_windows_exe(source, output, python_root)

            self.assertTrue(built.samefile(output))
            self.assertTrue(target._is_x86_executable(output))


if __name__ == "__main__":
    unittest.main()
