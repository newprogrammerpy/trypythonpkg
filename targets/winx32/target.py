"""Probe and build a Windows x86 executable with an installed x86 CPython."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Capability:
    ready: bool
    message: str
    compiler: str | None = None
    resource_compiler: str | None = None
    python_root: Path | None = None


class TargetBuildError(RuntimeError):
    """The winx32 executable cannot be built with the available inputs."""


def _find_vcvarsall() -> Path | None:
    candidates: list[Path] = []
    configured_vcvarsall = os.environ.get("WINX32_VCVARSALL", "").strip()
    if configured_vcvarsall:
        candidate = Path(configured_vcvarsall).expanduser()
        return candidate.resolve() if candidate.is_file() else None

    for variable in ("VSINSTALLDIR", "VS170COMNTOOLS", "VS160COMNTOOLS"):
        value = os.environ.get(variable)
        if value:
            root = Path(value)
            if root.name.lower() == "common7":
                root = root.parent
            candidates.append(root / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat")

    program_files = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        try:
            result = subprocess.run(
                [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            candidates.append(
                Path(result.stdout.strip()) / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
            )

    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _python_root_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured_root = os.environ.get("WINX32_PYTHON_ROOT", "").strip()
    if configured_root:
        candidates.append(Path(configured_root).expanduser())

    if os.name == "nt":
        import winreg

        for hive, key_path in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Python\PythonCore"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Python\PythonCore"),
        ):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        version = winreg.EnumKey(key, index)
                        install_key = key_path + "\\" + version + r"\InstallPath"
                        try:
                            with winreg.OpenKey(hive, install_key) as install:
                                location, _ = winreg.QueryValueEx(install, None)
                        except OSError:
                            continue
                        candidates.append(Path(location))
            except OSError:
                continue

    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _is_x86_executable(path: Path) -> bool:
    try:
        with path.open("rb") as executable:
            dos_header = executable.read(64)
            if len(dos_header) < 64 or dos_header[:2] != b"MZ":
                return False
            pe_offset = int.from_bytes(dos_header[0x3C:0x40], "little")
            executable.seek(pe_offset)
            return executable.read(6) == b"PE\0\0\x4c\x01"
    except OSError:
        return False


def _validate_python_root(python_root: Path) -> str | None:
    if not python_root.is_dir():
        return f"CPython root does not exist: {python_root}"
    if not _is_x86_executable(python_root / "python.exe"):
        return "CPython root must contain a valid 32-bit x86 python.exe."
    versioned_dlls = [
        path for path in python_root.glob("python*.dll")
        if path.name.lower() != "python3.dll"
        and re.fullmatch(r"python\d+\.dll", path.name, re.IGNORECASE)
    ]
    if not versioned_dlls:
        return "CPython root must contain its versioned python DLL (for example python314.dll)."
    missing = [
        name for name in (
            "python3.dll",
            "vcruntime140.dll",
            "LICENSE.txt",
        )
        if not (python_root / name).is_file()
    ]
    missing.extend(
        name for name in ("Lib", "DLLs")
        if not (python_root / name).is_dir()
    )
    if missing:
        return "CPython root is incomplete; missing " + ", ".join(missing) + "."
    runtime_binaries = [
        path
        for path in python_root.glob("*.dll")
    ]
    for directory in (python_root / "DLLs", python_root / "Lib"):
        runtime_binaries.extend(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in {".dll", ".pyd"}
        )
    wrong_architecture = [
        str(path.relative_to(python_root))
        for path in runtime_binaries
        if not _is_x86_executable(path)
    ]
    if wrong_architecture:
        return "CPython runtime contains non-x86 DLL/extension binaries: " + ", ".join(
            wrong_architecture[:5]
        )
    return None


def _stage_x86_runtime(python_root: Path, stage: Path) -> str:
    import pypack

    versioned_dll = pypack._versioned_python_dll(python_root)
    if versioned_dll is None:
        raise TargetBuildError(f"Could not identify the versioned Python DLL in {python_root}")
    if not (python_root / "Lib").is_dir() or not (python_root / "DLLs").is_dir():
        raise TargetBuildError("The x86 CPython directory must contain Lib and DLLs.")

    # x86 CPython uses vcruntime140.dll; vcruntime140_1.dll is x64-only.
    for name in ("python.exe", "python3.dll", versioned_dll.name,
                 "vcruntime140.dll", "LICENSE.txt"):
        pypack._copy_file(python_root / name, stage / name)

    dlls_destination = stage / "DLLs"
    for path in sorted((python_root / "DLLs").rglob("*")):
        if path.is_file():
            pypack._copy_file(path, dlls_destination / path.relative_to(python_root / "DLLs"))

    lib_directory = python_root / "Lib"
    zip_name = f"{versioned_dll.stem}.zip"
    with zipfile.ZipFile(stage / zip_name, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(lib_directory.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(lib_directory)
            if path.suffix.lower() in {".pyd", ".dll"}:
                pypack._copy_file(path, stage / "Lib" / relative)
            else:
                archive.write(path, relative.as_posix())

    pth_name = f"{versioned_dll.stem}._pth"
    pth_content = "\n".join((zip_name, ".", "DLLs", "Lib", "app", "import site", ""))
    (stage / pth_name).write_text(pth_content, encoding="ascii")
    (stage / "app").mkdir()
    return zip_name


def _find_python_root(python_root: Path | None = None) -> tuple[Path | None, str | None]:
    candidates = [python_root.resolve()] if python_root is not None else _python_root_candidates()
    if not candidates:
        return None, (
            "No 32-bit CPython runtime was found. Install or provide a full x86 CPython "
            "directory with WINX32_PYTHON_ROOT or --python-root."
        )

    failures: list[str] = []
    for candidate in candidates:
        error = _validate_python_root(candidate)
        if error is None:
            return candidate, None
        failures.append(f"{candidate}: {error}")
    return None, "No valid x86 CPython runtime was found. " + failures[0]


def _parse_environment(output: str) -> dict[str, str]:
    environment = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            environment[key] = value
    return environment


def _configure_x86_environment(vcvarsall: Path) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="winx32-env-") as directory:
        batch_file = Path(directory) / "configure.cmd"
        batch_file.write_text(
            "\r\n".join(
                (
                    "@echo off",
                    f'call "{vcvarsall}" x86 >nul',
                    "if errorlevel 1 exit /b %errorlevel%",
                    "set",
                    "",
                )
            ),
            encoding="ascii",
        )
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(batch_file)],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"vcvarsall.bat x86 failed: {detail or result.returncode}")
    return _parse_environment(result.stdout)


def _smoke_test(compiler: str, resource_compiler: str, environment: Mapping[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="winx32-toolchain-") as directory:
        work = Path(directory)
        source = work / "probe.c"
        executable = work / "probe.exe"
        resource_script = work / "probe.rc"
        resource_data = work / "payload.bin"
        resource = work / "probe.res"
        source.write_text("int main(void) { return 0; }\n", encoding="ascii")
        resource_data.write_bytes(b"x")
        resource_script.write_text('1 RCDATA "payload.bin"\n', encoding="ascii")

        compile_result = subprocess.run(
            [compiler, "/nologo", "/W4", "/WX", f"/Fe:{executable}", str(source)],
            cwd=work,
            env=dict(environment),
            capture_output=True,
            text=True,
            check=False,
        )
        if compile_result.returncode != 0 or not executable.is_file():
            detail = (compile_result.stdout + compile_result.stderr).strip()
            raise RuntimeError(f"MSVC x86 smoke compile failed: {detail or compile_result.returncode}")
        if not _is_x86_executable(executable):
            raise RuntimeError("MSVC smoke compile did not produce an x86 executable.")

        resource_result = subprocess.run(
            [resource_compiler, "/nologo", f"/fo{resource}", str(resource_script)],
            cwd=work,
            env=dict(environment),
            capture_output=True,
            text=True,
            check=False,
        )
        if resource_result.returncode != 0 or not resource.is_file():
            detail = (resource_result.stdout + resource_result.stderr).strip()
            raise RuntimeError(f"Windows SDK resource compiler failed: {detail or resource_result.returncode}")


def _discover_toolchain() -> tuple[str, str, dict[str, str]]:
    vcvarsall = _find_vcvarsall()
    if vcvarsall is None:
        configured_vcvarsall = os.environ.get("WINX32_VCVARSALL", "").strip()
        if configured_vcvarsall:
            raise TargetBuildError(
                f"WINX32_VCVARSALL does not point to an existing file: {configured_vcvarsall}"
            )
        raise TargetBuildError(
            "MSVC Build Tools with VC\\Auxiliary\\Build\\vcvarsall.bat were not found. "
            "Set WINX32_VCVARSALL to its full path if Visual Studio is installed elsewhere."
        )
    try:
        environment = _configure_x86_environment(vcvarsall)
    except (OSError, RuntimeError) as error:
        raise TargetBuildError(str(error)) from error

    compiler = shutil.which("cl.exe", path=environment.get("PATH", ""))
    resource_compiler = shutil.which("rc.exe", path=environment.get("PATH", ""))
    missing = [
        name
        for name, path in (("cl.exe", compiler), ("rc.exe", resource_compiler))
        if path is None
    ]
    if missing:
        raise TargetBuildError(
            f"The x86 developer environment is missing {', '.join(missing)}."
        )
    try:
        _smoke_test(compiler, resource_compiler, environment)
    except (OSError, RuntimeError) as error:
        raise TargetBuildError(str(error)) from error
    return compiler, resource_compiler, environment


def probe(platform: str | None = None, python_root: Path | None = None) -> Capability:
    current_platform = sys.platform if platform is None else platform
    if current_platform != "win32":
        return Capability(False, f"winx32 requires Windows; detected {current_platform}.")

    try:
        compiler, resource_compiler, _ = _discover_toolchain()
    except (OSError, TargetBuildError) as error:
        return Capability(False, str(error))

    try:
        resolved_python_root, python_error = _find_python_root(python_root)
    except OSError as error:
        return Capability(False, f"Could not inspect the CPython runtime: {error}", compiler, resource_compiler)
    if python_error:
        return Capability(
            False,
            f"MSVC x86 toolchain is ready, but {python_error}",
            compiler,
            resource_compiler,
        )

    return Capability(
        True,
        "MSVC x86, Windows SDK resource compiler, and x86 CPython runtime are ready.",
        compiler,
        resource_compiler,
        resolved_python_root,
    )


def build_windows_exe(
    source_path: Path,
    output_path: Path | None = None,
    python_root: Path | None = None,
    include_paths: list[Path] | None = None,
) -> Path:
    """Build an x86 executable without changing the shared x64 packager."""
    if sys.platform != "win32":
        raise TargetBuildError("Building a winx32 executable requires Windows.")

    source_path = source_path.resolve()
    output_path = (output_path or source_path.with_suffix(".exe")).resolve()
    packager_path = Path(__file__).resolve().parents[2] / "pypack.py"
    if not source_path.is_file() or source_path.suffix.lower() != ".py":
        raise TargetBuildError(f"Source must be an existing .py file: {source_path}")
    if output_path in {source_path, Path(__file__).resolve(), packager_path}:
        raise TargetBuildError("The output cannot overwrite the source or target adapter.")

    resolved_python_root, python_error = _find_python_root(python_root)
    if python_error or resolved_python_root is None:
        raise TargetBuildError(python_error or "An x86 CPython runtime is required.")

    try:
        compiler, resource_compiler, environment = _discover_toolchain()
    except (OSError, TargetBuildError) as error:
        raise TargetBuildError(str(error)) from error

    import pypack

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pypack-winx32-") as temporary:
        work = Path(temporary)
        stage = work / "payload"
        stage.mkdir()
        try:
            _stage_x86_runtime(resolved_python_root, stage)
            pypack._copy_file(source_path, stage / "app" / "__main__.py")
            for relative_name, path in pypack._collect_application_files(
                source_path, include_paths, excluded_paths={output_path}
            ).items():
                pypack._copy_file(path, stage / "app" / Path(relative_name))
        except pypack.PackageError as error:
            raise TargetBuildError(str(error)) from error

        files = sorted(path for path in stage.rglob("*") if path.is_file())
        if len(files) >= 65535:
            raise TargetBuildError("The runtime contains too many files to embed as Windows resources.")

        resource_script = work / "payload.rc"
        resource_lines = [
            f'{resource_id} PYDATA {pypack._rc_string(path)}'
            for resource_id, path in enumerate(files, start=1)
        ]
        resource_script.write_text("\n".join(resource_lines) + "\n", encoding="utf-8-sig")
        c_source = work / "launcher.c"
        c_source.write_text(pypack._launcher_source(files, stage), encoding="utf-8")
        resource_object = work / "payload.res"
        temporary_exe = work / "output.exe"

        resource_result = subprocess.run(
            [resource_compiler, "/nologo", f"/fo{resource_object}", str(resource_script)],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if resource_result.returncode != 0 or not resource_object.is_file():
            details = (resource_result.stdout + resource_result.stderr).strip()
            raise TargetBuildError(
                f"Windows SDK resource compilation failed:\n{details or resource_result.returncode}"
            )

        compile_result = subprocess.run(
            [
                compiler,
                "/nologo",
                "/O2",
                "/W4",
                "/DUNICODE",
                "/D_UNICODE",
                str(c_source),
                str(resource_object),
                "/link",
                f"/OUT:{temporary_exe}",
            ],
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if compile_result.returncode != 0 or not temporary_exe.is_file():
            details = (compile_result.stdout + compile_result.stderr).strip()
            raise TargetBuildError(
                f"MSVC x86 executable build failed:\n{details or compile_result.returncode}"
            )
        if not _is_x86_executable(temporary_exe):
            raise TargetBuildError("MSVC did not produce a valid x86 executable.")
        os.replace(temporary_exe, output_path)
    return output_path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    probe_parser = commands.add_parser("probe", help="check the x86 build prerequisites")
    probe_parser.add_argument("--python-root", type=Path)
    build_parser = commands.add_parser("build", help="build a standalone winx32 executable")
    build_parser.add_argument("source", type=Path)
    build_parser.add_argument("output", type=Path, nargs="?")
    build_parser.add_argument("--python-root", type=Path)
    build_parser.add_argument(
        "--include", type=Path, action="append", default=[], metavar="PATH"
    )
    args = parser.parse_args()

    if args.command == "build":
        try:
            output = build_windows_exe(
                args.source, args.output, args.python_root, args.include
            )
        except (OSError, TargetBuildError, ValueError) as error:
            print(f"winx32 build failed: {error}", file=sys.stderr)
            return 1
        print(f"Created {output}")
        return 0

    result = probe(python_root=getattr(args, "python_root", None))
    status = "ready" if result.ready else "unavailable"
    print(f"winx32 target: {status}: {result.message}")
    if result.compiler:
        print(f"cl.exe: {result.compiler}")
    if result.resource_compiler:
        print(f"rc.exe: {result.resource_compiler}")
    if result.python_root:
        print(f"CPython x86: {result.python_root}")
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
