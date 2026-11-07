import contextlib
import hashlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pypack


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_create_verify_and_run_package(self):
        source = self.directory / "sample.py"
        package = self.directory / "sample.pypkg"
        source.write_text(
            "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )

        pypack.create_package(source, package)
        pypack.verify_package(package)

        original_argv = sys.argv
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            pypack.run_package(package, ["two words", "quote\"here"])

        self.assertEqual(json.loads(output.getvalue()), ["two words", "quote\"here"])
        self.assertIs(sys.argv, original_argv)

    def test_create_and_run_package_with_nested_module(self):
        source = self.directory / "main.py"
        helper = self.directory / "pkg" / "helper.py"
        helper.parent.mkdir()
        source.write_text(
            "from pkg.helper import greeting\nprint(greeting())\n",
            encoding="utf-8",
        )
        helper.write_text("def greeting():\n    return 'hello from module'\n", encoding="utf-8")
        package = self.directory / "sample.pypkg"

        pypack.create_package(source, package, [Path("pkg")])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            pypack.run_package(package, [])

        self.assertEqual(output.getvalue(), "hello from module\n")

    def test_create_package_rejects_reserved_manifest_include(self):
        source = self.directory / "main.py"
        reserved = self.directory / pypack.MANIFEST_NAME
        source.write_text("print('main')\n", encoding="utf-8")
        reserved.write_text("not a package manifest\n", encoding="utf-8")

        with self.assertRaisesRegex(pypack.PackageError, "duplicate name"):
            pypack.create_package(
                source,
                self.directory / "sample.pypkg",
                [Path(pypack.MANIFEST_NAME)],
            )

    def test_create_package_creates_missing_output_directory(self):
        source = self.directory / "main.py"
        package = self.directory / "dist" / "nested" / "sample.pypkg"
        source.write_text("print('main')\n", encoding="utf-8")

        pypack.create_package(source, package)

        self.assertTrue(package.is_file())
        pypack.verify_package(package)

    def test_rejects_modified_entry_file(self):
        source = self.directory / "sample.py"
        package = self.directory / "sample.pypkg"
        source.write_text("print('original')\n", encoding="utf-8")
        pypack.create_package(source, package)

        replacement = self.directory / "replacement.pypkg"
        with zipfile.ZipFile(package) as original, zipfile.ZipFile(replacement, "w") as changed:
            changed.writestr(pypack.MANIFEST_NAME, original.read(pypack.MANIFEST_NAME))
            changed.writestr(pypack.ENTRY_NAME, b"print('changed')\n")

        with self.assertRaisesRegex(pypack.PackageError, "SHA-256"):
            pypack.verify_package(replacement)

    def test_rejects_unexpected_archive_member(self):
        package = self.directory / "unexpected.pypkg"
        manifest = {
            "format": pypack.FORMAT_NAME,
            "version": pypack.LEGACY_FORMAT_VERSION,
            "entry": pypack.ENTRY_NAME,
        }
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(pypack.MANIFEST_NAME, json.dumps(manifest))
            archive.writestr(pypack.ENTRY_NAME, b"")
            archive.writestr("extra.txt", b"unexpected")

        with self.assertRaisesRegex(pypack.PackageError, "only its manifest"):
            pypack.verify_package(package)

    def test_verifies_legacy_package(self):
        package = self.directory / "legacy.pypkg"
        source = b"print('legacy')\n"
        manifest = {
            "format": pypack.FORMAT_NAME,
            "version": pypack.LEGACY_FORMAT_VERSION,
            "entry": pypack.ENTRY_NAME,
            "sha256": hashlib.sha256(source).hexdigest(),
        }
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(pypack.MANIFEST_NAME, json.dumps(manifest))
            archive.writestr(pypack.ENTRY_NAME, source)

        pypack.verify_package(package)

    def test_rejects_boolean_package_version(self):
        package = self.directory / "boolean-version.pypkg"
        manifest = {
            "format": pypack.FORMAT_NAME,
            "version": True,
            "entry": pypack.ENTRY_NAME,
            "files": {pypack.ENTRY_NAME: hashlib.sha256(b"").hexdigest()},
        }
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(pypack.MANIFEST_NAME, json.dumps(manifest))
            archive.writestr(pypack.ENTRY_NAME, b"")

        with self.assertRaisesRegex(pypack.PackageError, "Unsupported package"):
            pypack.verify_package(package)

    def test_rejects_include_outside_project_root(self):
        source = self.directory / "main.py"
        outside = self.directory.parent / "outside.py"
        source.write_text("print('main')\n", encoding="utf-8")
        outside.write_text("print('outside')\n", encoding="utf-8")
        self.addCleanup(outside.unlink)

        with self.assertRaisesRegex(pypack.PackageError, "stay inside"):
            pypack.create_package(source, self.directory / "sample.pypkg", [Path("../outside.py")])

    def test_stage_runtime_keeps_extensions_outside_library_zip(self):
        python_root = self.directory / "python"
        stage = self.directory / "stage"
        for name in (
            "python.exe",
            "python3.dll",
            "python314.dll",
            "vcruntime140.dll",
            "vcruntime140_1.dll",
            "LICENSE.txt",
        ):
            path = python_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"runtime")

        (python_root / "DLLs").mkdir()
        (python_root / "DLLs" / "_ssl.pyd").write_bytes(b"extension")
        (python_root / "Lib" / "encodings").mkdir(parents=True)
        (python_root / "Lib" / "encodings" / "__init__.py").write_text("", encoding="ascii")
        (python_root / "Lib" / "__pycache__").mkdir()
        (python_root / "Lib" / "__pycache__" / "cached.pyc").write_bytes(b"bytecode")

        zip_name = pypack._stage_runtime(python_root, stage)

        self.assertTrue((stage / "DLLs" / "_ssl.pyd").is_file())
        with zipfile.ZipFile(stage / zip_name) as archive:
            names = archive.namelist()
        self.assertIn("encodings/__init__.py", names)
        self.assertFalse(any("__pycache__" in item for item in names))
        self.assertEqual(
            (stage / "python314._pth").read_text(encoding="ascii"),
            f"{zip_name}\n.\nDLLs\nLib\napp\nimport site\n",
        )

    def test_pe_machine_reads_x64_and_x86_headers(self):
        executable = self.directory / "probe.exe"
        header = bytearray(64)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 0x3C, 64)

        executable.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0x8664))
        self.assertEqual(pypack._pe_machine(executable), 0x8664)

        executable.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0x014C))
        self.assertEqual(pypack._pe_machine(executable), 0x014C)

        executable.write_bytes(b"not a PE file")
        self.assertIsNone(pypack._pe_machine(executable))

    def test_exe_build_rejects_x86_python_runtime(self):
        source = self.directory / "main.py"
        python_root = self.directory / "python"
        source.write_text("print('hello')\n", encoding="utf-8")
        python_root.mkdir()
        header = bytearray(64)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 0x3C, 64)
        x86_pe = header + b"PE\0\0" + struct.pack("<H", 0x014C)
        (python_root / "python.exe").write_bytes(x86_pe)
        (python_root / "python314.dll").write_bytes(x86_pe)

        with self.assertRaisesRegex(pypack.PackageError, "Windows x64"):
            pypack.build_windows_exe(
                source,
                self.directory / "main.exe",
                python_root,
            )

    def test_run_forwards_help_option_as_program_argument(self):
        with (
            patch(
                "sys.argv",
                ["pypack.py", "run", str(self.directory / "program.pypkg"), "--help"],
            ),
            patch.object(pypack, "run_package") as run_package,
        ):
            result = pypack.main()

        self.assertEqual(result, 0)
        run_package.assert_called_once_with(
            (self.directory / "program.pypkg").resolve(),
            ["--help"],
        )

    def test_wix7_msi_source_describes_per_user_x64_install(self):
        executable = self.directory / 'My "App".exe'
        source = pypack._wix7_source(
            executable,
            'My "App" & Tool',
            "Example Company",
            "2.4.1",
        )
        root = ET.fromstring(source)
        namespace = {"wix": "http://wixtoolset.org/schemas/v4/wxs"}
        package = root.find("wix:Package", namespace)

        self.assertIsNotNone(package)
        self.assertEqual(package.attrib["Version"], "2.4.1")
        self.assertEqual(package.attrib["Manufacturer"], "Example Company")
        self.assertNotIn("Platform", package.attrib)
        self.assertEqual(package.attrib["Scope"], "perUser")
        self.assertIsNotNone(package.find("wix:MajorUpgrade", namespace))
        file_element = root.find(".//wix:File", namespace)
        self.assertEqual(file_element.attrib["Source"], str(executable))
        self.assertEqual(file_element.attrib["Name"], executable.name)

    def test_msi_rejects_non_x64_executable(self):
        executable = self.directory / "sample.exe"
        header = bytearray(64)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 0x3C, 64)
        executable.write_bytes(header + b"PE\0\0" + struct.pack("<H", 0x014C))

        with patch("pypack.sys.platform", "win32"):
            with self.assertRaisesRegex(pypack.PackageError, "Windows x64"):
                pypack.build_windows_msi(
                    executable,
                    self.directory / "sample.msi",
                )


if __name__ == "__main__":
    unittest.main()
