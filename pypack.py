"""Package Python programs without PyInstaller or Nuitka."""

import argparse
import hashlib
import json
import os
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr


FORMAT_NAME = "pypkg"
FORMAT_VERSION = 2
LEGACY_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
ENTRY_NAME = "__main__.py"

LAUNCHER_SOURCE = r"""
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

typedef struct {
    unsigned short id;
    const wchar_t *path;
} PayloadFile;

static const PayloadFile payload_files[] = {
@@PAYLOAD_TABLE@@
};
static const size_t payload_count = sizeof(payload_files) / sizeof(payload_files[0]);

static BOOL append_char(wchar_t *buffer, size_t capacity, size_t *length, wchar_t value) {
    if (*length + 1 >= capacity) return FALSE;
    buffer[(*length)++] = value;
    buffer[*length] = L'\0';
    return TRUE;
}

static BOOL append_argument(wchar_t *buffer, size_t capacity, size_t *length,
                            const wchar_t *argument) {
    const wchar_t *cursor = argument;
    if (!append_char(buffer, capacity, length, L'"')) return FALSE;

    while (*cursor) {
        size_t backslashes = 0;
        while (*cursor == L'\\') {
            ++backslashes;
            ++cursor;
        }

        if (*cursor == L'"') {
            for (size_t i = 0; i < backslashes * 2 + 1; ++i)
                if (!append_char(buffer, capacity, length, L'\\')) return FALSE;
            if (!append_char(buffer, capacity, length, L'"')) return FALSE;
            ++cursor;
        } else if (*cursor == L'\0') {
            for (size_t i = 0; i < backslashes * 2; ++i)
                if (!append_char(buffer, capacity, length, L'\\')) return FALSE;
        } else {
            for (size_t i = 0; i < backslashes; ++i)
                if (!append_char(buffer, capacity, length, L'\\')) return FALSE;
            if (!append_char(buffer, capacity, length, *cursor++)) return FALSE;
        }
    }

    return append_char(buffer, capacity, length, L'"');
}

static BOOL ensure_parent_directories(const wchar_t *file_path) {
    wchar_t path[32768];
    if (wcslen(file_path) >= sizeof(path) / sizeof(path[0])) return FALSE;
    wcscpy_s(path, sizeof(path) / sizeof(path[0]), file_path);

    for (wchar_t *cursor = path + 3; *cursor; ++cursor) {
        if (*cursor == L'\\' || *cursor == L'/') {
            wchar_t saved = *cursor;
            *cursor = L'\0';
            if (!CreateDirectoryW(path, NULL) && GetLastError() != ERROR_ALREADY_EXISTS)
                return FALSE;
            *cursor = saved;
        }
    }
    return TRUE;
}

static BOOL extract_payload(const wchar_t *root) {
    HMODULE module = GetModuleHandleW(NULL);
    for (size_t i = 0; i < payload_count; ++i) {
        wchar_t destination[32768];
        if (swprintf_s(destination, sizeof(destination) / sizeof(destination[0]),
                       L"%s\\%s", root, payload_files[i].path) < 0)
            return FALSE;

        HRSRC resource = FindResourceW(module, MAKEINTRESOURCEW(payload_files[i].id), L"PYDATA");
        if (!resource) return FALSE;
        HGLOBAL loaded = LoadResource(module, resource);
        DWORD size = SizeofResource(module, resource);
        const void *data = loaded ? LockResource(loaded) : NULL;
        if (!data || !ensure_parent_directories(destination)) return FALSE;

        HANDLE file = CreateFileW(destination, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                                  FILE_ATTRIBUTE_NORMAL, NULL);
        if (file == INVALID_HANDLE_VALUE) return FALSE;

        DWORD written = 0;
        BOOL success = WriteFile(file, data, size, &written, NULL) && written == size;
        CloseHandle(file);
        if (!success) return FALSE;
    }
    return TRUE;
}

static void remove_tree(const wchar_t *directory) {
    wchar_t pattern[32768];
    if (swprintf_s(pattern, sizeof(pattern) / sizeof(pattern[0]), L"%s\\*", directory) < 0)
        return;

    WIN32_FIND_DATAW entry;
    HANDLE search = FindFirstFileW(pattern, &entry);
    if (search != INVALID_HANDLE_VALUE) {
        do {
            if (wcscmp(entry.cFileName, L".") == 0 || wcscmp(entry.cFileName, L"..") == 0)
                continue;

            wchar_t child[32768];
            if (swprintf_s(child, sizeof(child) / sizeof(child[0]), L"%s\\%s",
                           directory, entry.cFileName) < 0)
                continue;
            if (entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                remove_tree(child);
            } else {
                SetFileAttributesW(child, FILE_ATTRIBUTE_NORMAL);
                DeleteFileW(child);
            }
        } while (FindNextFileW(search, &entry));
        FindClose(search);
    }
    RemoveDirectoryW(directory);
}

int wmain(int argc, wchar_t **argv) {
    wchar_t temporary_path[MAX_PATH];
    wchar_t root[MAX_PATH];
    DWORD temporary_length = GetTempPathW(MAX_PATH, temporary_path);
    if (temporary_length == 0 || temporary_length >= MAX_PATH ||
        !GetTempFileNameW(temporary_path, L"pyp", 0, root)) {
        fwprintf(stderr, L"Could not create a temporary directory.\n");
        return 1;
    }
    DeleteFileW(root);
    if (!CreateDirectoryW(root, NULL)) {
        fwprintf(stderr, L"Could not create the runtime directory.\n");
        return 1;
    }

    if (!extract_payload(root)) {
        fwprintf(stderr, L"Could not extract the bundled Python runtime.\n");
        remove_tree(root);
        return 1;
    }

    wchar_t python_path[MAX_PATH];
    wchar_t script_path[MAX_PATH];
    if (swprintf_s(python_path, MAX_PATH, L"%s\\python.exe", root) < 0 ||
        swprintf_s(script_path, MAX_PATH, L"%s\\app\\__main__.py", root) < 0) {
        fwprintf(stderr, L"Temporary runtime path is too long.\n");
        remove_tree(root);
        return 1;
    }

    size_t capacity = 32768;
    wchar_t *command = (wchar_t *)calloc(capacity, sizeof(wchar_t));
    if (!command) {
        remove_tree(root);
        return 1;
    }

    size_t length = 0;
    BOOL command_ok = append_argument(command, capacity, &length, python_path) &&
                      append_char(command, capacity, &length, L' ') &&
                      append_argument(command, capacity, &length, L"-X") &&
                      append_char(command, capacity, &length, L' ') &&
                      append_argument(command, capacity, &length, L"utf8") &&
                      append_char(command, capacity, &length, L' ') &&
                      append_argument(command, capacity, &length, script_path);
    for (int i = 1; command_ok && i < argc; ++i) {
        command_ok = append_char(command, capacity, &length, L' ') &&
                     append_argument(command, capacity, &length, argv[i]);
    }
    if (!command_ok) {
        fwprintf(stderr, L"Command line is too long.\n");
        free(command);
        remove_tree(root);
        return 1;
    }

    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    BOOL started = CreateProcessW(python_path, command, NULL, NULL, FALSE, 0,
                                  NULL, NULL, &startup, &process);
    free(command);
    if (!started) {
        fwprintf(stderr, L"Could not start bundled Python (error %lu).\n", GetLastError());
        remove_tree(root);
        return 1;
    }

    DWORD wait_result = WaitForSingleObject(process.hProcess, INFINITE);
    if (wait_result != WAIT_OBJECT_0) {
        DWORD error = GetLastError();
        fwprintf(stderr, L"Could not wait for bundled Python (error %lu).\n", error);
        TerminateProcess(process.hProcess, 1);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        remove_tree(root);
        return 1;
    }

    DWORD exit_code = 1;
    if (!GetExitCodeProcess(process.hProcess, &exit_code)) {
        fwprintf(stderr, L"Could not read bundled Python's exit code (error %lu).\n",
                 GetLastError());
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    remove_tree(root);
    return (int)exit_code;
}
"""


class PackageError(Exception):
    """Raised when a package is malformed or its contents do not match."""


def _collect_application_files(
    source_path: Path,
    include_paths: list[Path] | None,
    excluded_paths: set[Path] | None = None,
) -> dict[str, Path]:
    source_path = source_path.resolve()
    project_root = source_path.parent
    excluded = {path.resolve() for path in (excluded_paths or set())}
    files: dict[str, Path] = {}
    folded_names = {ENTRY_NAME.casefold(), MANIFEST_NAME.casefold()}

    for include_path in include_paths or []:
        candidate = include_path if include_path.is_absolute() else project_root / include_path
        candidate = candidate.resolve()
        if not candidate.is_relative_to(project_root):
            raise PackageError(f"Included paths must stay inside {project_root}: {include_path}")
        if not candidate.exists():
            raise PackageError(f"Included path does not exist: {include_path}")

        candidates = [candidate] if candidate.is_file() else (
            sorted(candidate.rglob("*")) if candidate.is_dir() else []
        )
        if not candidates and not candidate.is_dir():
            raise PackageError(f"Included path is not a file or directory: {include_path}")

        for path in candidates:
            if not path.is_file():
                continue
            resolved_path = path.resolve()
            if not resolved_path.is_relative_to(project_root):
                raise PackageError(f"Included file resolves outside {project_root}: {path}")
            if resolved_path == source_path or resolved_path in excluded:
                continue
            if "__pycache__" in path.parts or path.suffix.lower() == ".pyc":
                continue

            relative_name = path.relative_to(project_root).as_posix()
            folded_name = relative_name.casefold()
            if folded_name in folded_names:
                raise PackageError(f"Included paths contain a duplicate name: {relative_name}")
            folded_names.add(folded_name)
            files[relative_name] = path

    return files


def create_package(
    source_path: Path,
    package_path: Path,
    include_paths: list[Path] | None = None,
) -> None:
    source_path = source_path.resolve()
    package_path = package_path.resolve()

    if not source_path.is_file() or source_path.suffix.lower() != ".py":
        raise PackageError(f"Source must be an existing .py file: {source_path}")
    if source_path == package_path:
        raise PackageError("The package output cannot overwrite the source file.")

    source = source_path.read_bytes()
    included_files = _collect_application_files(
        source_path, include_paths, excluded_paths={package_path}
    )
    bundled_sources = {ENTRY_NAME: source}
    bundled_sources.update({
        name: path.read_bytes() for name, path in included_files.items()
    })
    manifest = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "entry": ENTRY_NAME,
        "source_name": source_path.name,
        "files": {
            name: hashlib.sha256(contents).hexdigest()
            for name, contents in bundled_sources.items()
        },
    }

    package_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, sort_keys=True))
        for name, contents in bundled_sources.items():
            archive.writestr(name, contents)


def verify_package(package_path: Path) -> None:
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise PackageError("Package contains duplicate archive paths.")
            manifest = json.loads(archive.read(MANIFEST_NAME))
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise PackageError(f"Cannot read package: {error}") from error

    if not isinstance(manifest, dict):
        raise PackageError("Invalid package manifest.")
    if manifest.get("format") != FORMAT_NAME:
        raise PackageError("Unsupported package format or version.")
    if manifest.get("entry") != ENTRY_NAME:
        raise PackageError("Invalid package entry point.")

    version = manifest.get("version")
    if type(version) is not int or version not in {LEGACY_FORMAT_VERSION, FORMAT_VERSION}:
        raise PackageError("Unsupported package format or version.")
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            if version == LEGACY_FORMAT_VERSION:
                if set(names) != {MANIFEST_NAME, ENTRY_NAME}:
                    raise PackageError(
                        "Package must contain only its manifest and Python entry file."
                    )
                expected_hashes = {
                    ENTRY_NAME: manifest.get("sha256"),
                }
            elif version == FORMAT_VERSION:
                expected_hashes = manifest.get("files")
                if not isinstance(expected_hashes, dict) or ENTRY_NAME not in expected_hashes:
                    raise PackageError("Invalid package file list.")
                for name in expected_hashes:
                    path_parts = name.split("/")
                    if (
                        not isinstance(name, str)
                        or not name
                        or "\\" in name
                        or name.startswith("/")
                        or any(part in {"", ".", ".."} for part in path_parts)
                        or name == MANIFEST_NAME
                    ):
                        raise PackageError(f"Invalid package path: {name!r}")
                if set(names) != {MANIFEST_NAME, *expected_hashes}:
                    raise PackageError("Package files do not match the manifest.")
            else:
                raise PackageError("Unsupported package format or version.")

            for name, expected_hash in expected_hashes.items():
                contents = archive.read(name)
                if expected_hash != hashlib.sha256(contents).hexdigest():
                    raise PackageError(
                        f"Package contents failed the SHA-256 integrity check: {name}"
                    )
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise PackageError(f"Cannot read package: {error}") from error


def run_package(package_path: Path, program_args: list[str]) -> None:
    package_path = package_path.resolve()
    verify_package(package_path)

    previous_argv = sys.argv
    try:
        sys.argv = [str(package_path), *program_args]
        runpy.run_path(str(package_path), run_name="__main__")
    finally:
        sys.argv = previous_argv


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise PackageError(f"Required Python runtime file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _pe_machine(path: Path) -> int | None:
    try:
        with path.open("rb") as executable:
            dos_header = executable.read(64)
            if len(dos_header) < 64 or dos_header[:2] != b"MZ":
                return None
            pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
            executable.seek(pe_offset)
            if executable.read(4) != b"PE\0\0":
                return None
            machine = executable.read(2)
            if len(machine) != 2:
                return None
            return struct.unpack("<H", machine)[0]
    except OSError:
        return None


def _versioned_python_dll(python_root: Path) -> Path | None:
    python_dlls = sorted(
        path for path in python_root.glob("python*.dll")
        if path.name.lower() not in {"python3.dll"}
    )
    return next((path for path in python_dlls if path.stem[6:].isdigit()), None)


def _stage_runtime(python_root: Path, stage: Path) -> str:
    python_root = python_root.resolve()
    versioned_dll = _versioned_python_dll(python_root)
    if versioned_dll is None:
        raise PackageError(f"Could not identify the versioned Python DLL in {python_root}")
    if not (python_root / "Lib").is_dir() or not (python_root / "DLLs").is_dir():
        raise PackageError(
            "The selected Python directory must contain Lib and DLLs; "
            "use a full 64-bit CPython installation."
        )

    for name in ("python.exe", "python3.dll", versioned_dll.name,
                 "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        _copy_file(python_root / name, stage / name)

    dlls_destination = stage / "DLLs"
    for path in sorted((python_root / "DLLs").rglob("*")):
        if path.is_file():
            _copy_file(path, dlls_destination / path.relative_to(python_root / "DLLs"))

    lib_directory = python_root / "Lib"
    zip_name = f"{versioned_dll.stem}.zip"
    with zipfile.ZipFile(stage / zip_name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(lib_directory.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(lib_directory)
            if path.suffix.lower() in {".pyd", ".dll"}:
                _copy_file(path, stage / "Lib" / relative)
            else:
                archive.write(path, relative.as_posix())

    pth_name = f"{versioned_dll.stem}._pth"
    pth_content = "\n".join((zip_name, ".", "DLLs", "Lib", "app", "import site", ""))
    (stage / pth_name).write_text(pth_content, encoding="ascii")

    app_directory = stage / "app"
    app_directory.mkdir()
    return zip_name


def _find_vcvars64() -> Path | None:
    candidates = []
    for variable in ("VSINSTALLDIR", "VS170COMNTOOLS", "VS160COMNTOOLS"):
        value = os.environ.get(variable)
        if value:
            candidate = Path(value)
            if candidate.name.lower() == "common7":
                candidate = candidate.parent
            candidates.append(candidate / "VC" / "Auxiliary" / "Build" / "vcvars64.bat")

    for path in (
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"),
        Path(r"C:\Program Files\Microsoft Visual Studio\Installer\vswhere.exe"),
    ):
        if path.is_file():
            result = subprocess.run(
                [str(path), "-latest", "-products", "*", "-property", "installationPath"],
                check=False, capture_output=True, text=True,
            )
            installation = result.stdout.strip()
            if result.returncode == 0 and installation:
                candidates.append(
                    Path(installation) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
                )

    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _c_string(value: str) -> str:
    return 'L"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rc_string(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _launcher_source(files: list[Path], stage: Path) -> str:
    rows = []
    for resource_id, path in enumerate(files, start=1):
        relative = str(path.relative_to(stage))
        rows.append(f"    {{{resource_id}, {_c_string(relative)}}},")
    return LAUNCHER_SOURCE.replace("@@PAYLOAD_TABLE@@", "\n".join(rows))


def build_windows_exe(
    source_path: Path,
    output_path: Path,
    python_root: Path,
    include_paths: list[Path] | None = None,
) -> None:
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise PackageError("Building a Windows x64 .exe requires 64-bit Python on Windows.")

    source_path = source_path.resolve()
    output_path = output_path.resolve()
    python_root = python_root.resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".py":
        raise PackageError(f"Source must be an existing .py file: {source_path}")
    if output_path in {source_path, Path(__file__).resolve()}:
        raise PackageError("The output cannot overwrite the source or packager.")
    versioned_dll = _versioned_python_dll(python_root)
    if versioned_dll is None:
        raise PackageError(f"Could not identify the versioned Python DLL in {python_root}")
    for runtime_file in (python_root / "python.exe", versioned_dll):
        if _pe_machine(runtime_file) != 0x8664:
            raise PackageError(
                f"Python runtime file is not a valid Windows x64 executable or DLL: {runtime_file}"
            )

    vcvars = _find_vcvars64()
    if vcvars is None and not (shutil.which("cl.exe") and shutil.which("rc.exe")):
        raise PackageError(
            "MSVC x64 and the Windows SDK resource compiler are required. "
            "Run this from an x64 Native Tools Command Prompt for Visual Studio."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pypack-build-") as temporary:
        work = Path(temporary)
        stage = work / "payload"
        stage.mkdir()
        _stage_runtime(python_root, stage)
        shutil.copy2(source_path, stage / "app" / "__main__.py")
        for relative_name, path in _collect_application_files(
            source_path, include_paths, excluded_paths={output_path}
        ).items():
            _copy_file(path, stage / "app" / Path(relative_name))

        files = sorted(path for path in stage.rglob("*") if path.is_file())
        if len(files) >= 65535:
            raise PackageError("The runtime contains too many files to embed as Windows resources.")

        resource_script = work / "payload.rc"
        resource_lines = [
            f'{resource_id} PYDATA {_rc_string(path)}'
            for resource_id, path in enumerate(files, start=1)
        ]
        resource_script.write_text("\n".join(resource_lines) + "\n", encoding="utf-8-sig")

        c_source = work / "launcher.c"
        c_source.write_text(_launcher_source(files, stage), encoding="utf-8")
        resource_object = work / "payload.res"
        temporary_exe = work / "output.exe"
        batch_file = work / "build.cmd"

        commands = ["@echo off"]
        if vcvars:
            commands.extend((
                f'call "{vcvars}" >nul',
                "if errorlevel 1 exit /b %errorlevel%",
            ))
        commands.extend((
            f'cd /d "{work}"',
            "if errorlevel 1 exit /b %errorlevel%",
            f'rc /nologo /fo"{resource_object}" "{resource_script}"',
            "if errorlevel 1 exit /b %errorlevel%",
            f'cl /nologo /O2 /W4 /DUNICODE /D_UNICODE "{c_source}" '
            f'"{resource_object}" /link /OUT:"{temporary_exe}"',
            "exit /b %errorlevel%",
        ))
        batch_file.write_text("\r\n".join(commands) + "\r\n", encoding="ascii")

        completed = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(batch_file)],
            capture_output=True, text=True, check=False,
        )
        if completed.returncode != 0:
            details = (completed.stdout + completed.stderr).strip()
            raise PackageError(f"Windows native build failed:\n{details}")
        if not temporary_exe.is_file():
            raise PackageError("The compiler finished without producing an executable.")
        temporary_output = tempfile.NamedTemporaryFile(
            prefix=output_path.name + ".",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        )
        temporary_output.close()
        staged_output = Path(temporary_output.name)
        try:
            shutil.copy2(temporary_exe, staged_output)
            os.replace(staged_output, output_path)
        finally:
            staged_output.unlink(missing_ok=True)


def _find_wix7() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent / "tools" / "wix.exe",
    ]
    path_entry = shutil.which("wix.exe") or shutil.which("wix")
    if path_entry:
        candidates.append(Path(path_entry))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        result = subprocess.run(
            [str(candidate), "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip().startswith("7."):
            return candidate
    return None


def _wix7_source(
    source_path: Path,
    product_name: str,
    manufacturer: str,
    version: str,
) -> str:
    upgrade_code = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"pypack-msi:{manufacturer.casefold()}:{product_name.casefold()}",
    )
    install_directory = "".join(
        "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
        for character in product_name
    ).strip(" .")
    if not install_directory:
        install_directory = "Application"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">
  <Package Name={quoteattr(product_name)} Manufacturer={quoteattr(manufacturer)}
           Version={quoteattr(version)} UpgradeCode="{upgrade_code}"
           Scope="perUser" InstallerVersion="500" Compressed="yes">
    <MajorUpgrade DowngradeErrorMessage="A newer version of this application is already installed." />
    <MediaTemplate EmbedCab="yes" />
    <Feature Id="MainFeature" Title={quoteattr(product_name)} Level="1">
      <ComponentRef Id="ApplicationExecutable" />
    </Feature>
  </Package>
  <Fragment>
    <StandardDirectory Id="LocalAppDataFolder">
      <Directory Id="INSTALLFOLDER" Name={quoteattr(install_directory)}>
        <Component Id="ApplicationExecutable" Guid="*">
          <File Id="ApplicationExe" Source={quoteattr(str(source_path))}
                Name={quoteattr(source_path.name)} KeyPath="yes" />
        </Component>
      </Directory>
    </StandardDirectory>
  </Fragment>
</Wix>
"""


def build_windows_msi(
    source_path: Path,
    output_path: Path,
    product_name: str | None = None,
    manufacturer: str = "pypack",
    version: str = "1.0.0",
) -> None:
    if sys.platform != "win32":
        raise PackageError("Building an MSI requires Windows.")

    source_path = source_path.resolve()
    output_path = output_path.resolve()
    if not source_path.is_file() or source_path.suffix.lower() != ".exe":
        raise PackageError(f"MSI source must be an existing .exe file: {source_path}")
    if _pe_machine(source_path) != 0x8664:
        raise PackageError(
            f"MSI source is not a valid Windows x64 executable: {source_path}"
        )
    if output_path in {source_path, Path(__file__).resolve()}:
        raise PackageError("The MSI output cannot overwrite the executable or packager.")
    if not manufacturer.strip():
        raise PackageError("Manufacturer must not be empty.")
    if not product_name:
        product_name = source_path.stem
    if not product_name.strip():
        raise PackageError("Product name must not be empty.")

    version_parts = version.split(".")
    if (
        len(version_parts) != 3
        or any(not part.isdecimal() or int(part) > 255 for part in version_parts)
    ):
        raise PackageError("MSI version must contain three numbers from 0 to 255 (for example 1.2.3).")

    wix = _find_wix7()
    if wix is None:
        raise PackageError(
            "WiX Toolset 7 was not found. Place wix.exe in the packager's tools directory "
            "or add WiX 7 to PATH."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pypack-msi-") as temporary:
        work = Path(temporary)
        source_file = work / "package.wxs"
        temporary_msi = work / "package.msi"
        source_file.write_text(
            _wix7_source(source_path, product_name, manufacturer, version),
            encoding="utf-8",
        )

        build_result = subprocess.run(
            [
                str(wix),
                "build",
                "-arch",
                "x64",
                "-out",
                str(temporary_msi),
                str(source_file),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if build_result.returncode != 0:
            details = (build_result.stdout + build_result.stderr).strip()
            raise PackageError(f"WiX 7 MSI build failed:\n{details}")
        if not temporary_msi.is_file():
            raise PackageError("WiX finished without producing an MSI.")
        os.replace(temporary_msi, output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package Python programs without PyInstaller or Nuitka."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="package a .py file and optional extra files")
    create.add_argument("source", type=Path)
    create.add_argument("output", type=Path, nargs="?")
    create.add_argument(
        "--include",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="include a file or directory next to the entry script (repeatable)",
    )

    exe = commands.add_parser(
        "exe",
        help="build a Windows x64 .exe with an embedded CPython runtime",
    )
    exe.add_argument("source", type=Path)
    exe.add_argument("output", type=Path, nargs="?")
    exe.add_argument(
        "--include",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="include a file or directory next to the entry script (repeatable)",
    )
    exe.add_argument(
        "--python-root",
        type=Path,
        default=Path(sys.base_prefix),
        help="64-bit CPython installation containing Lib and DLLs",
    )

    msi = commands.add_parser(
        "msi",
        help="package a standalone .exe as a per-user Windows x64 MSI using WiX 7",
    )
    msi.add_argument("source", type=Path, help="standalone Windows .exe to install")
    msi.add_argument("output", type=Path, nargs="?")
    msi.add_argument("--name", help="application name shown in Windows")
    msi.add_argument("--manufacturer", default="pypack")
    msi.add_argument("--version", default="1.0.0")

    run = commands.add_parser("run", help="verify and run a .pypkg")
    run.add_argument("package", type=Path)
    run.add_argument("program_args", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "create":
            output = args.output or args.source.with_suffix(".pypkg")
            create_package(args.source, output, args.include)
            print(f"Created {output}")
            return 0

        if args.command == "exe":
            output = args.output or args.source.with_suffix(".exe")
            build_windows_exe(args.source, output, args.python_root, args.include)
            print(f"Created {output}")
            return 0

        if args.command == "msi":
            output = args.output or args.source.with_suffix(".msi")
            build_windows_msi(
                args.source,
                output,
                product_name=args.name,
                manufacturer=args.manufacturer,
                version=args.version,
            )
            print(f"Created {output}")
            return 0

        run_package(args.package, args.program_args)
        return 0
    except PackageError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
