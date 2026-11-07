"""Build a macOS ARM64 .app from a CPython framework and Python source."""

import argparse
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class BuildError(Exception):
    """Raised when the requested macOS application cannot be built."""


LAUNCHER_SOURCE = r"""
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    uint32_t executable_size = 0;
    _NSGetExecutablePath(NULL, &executable_size);
    char *raw_executable = malloc(executable_size);
    char executable[PATH_MAX];
    if (!raw_executable ||
        _NSGetExecutablePath(raw_executable, &executable_size) != 0 ||
        !realpath(raw_executable, executable)) {
        fprintf(stderr, "Could not resolve application executable path.\n");
        free(raw_executable);
        return 1;
    }
    free(raw_executable);

    char *macos_directory = strrchr(executable, '/');
    if (!macos_directory) {
        fprintf(stderr, "Invalid application executable path.\n");
        return 1;
    }
    *macos_directory = '\0';
    char *contents_directory = strrchr(executable, '/');
    if (!contents_directory) {
        fprintf(stderr, "Invalid application bundle layout.\n");
        return 1;
    }
    *contents_directory = '\0';

    char python_home[PATH_MAX];
    char python_executable[PATH_MAX];
    char application_script[PATH_MAX];
    char application_path[PATH_MAX];
    if (snprintf(python_home, sizeof(python_home),
                 "%s/Frameworks/Python.framework/Versions/%s",
                 executable, "@@PYTHON_VERSION@@") >= (int)sizeof(python_home) ||
        snprintf(python_executable, sizeof(python_executable),
                 "%s/bin/python3", python_home) >= (int)sizeof(python_executable) ||
        snprintf(application_path, sizeof(application_path),
                 "%s/Resources/app", executable) >= (int)sizeof(application_path) ||
        snprintf(application_script, sizeof(application_script),
                 "%s/@@SCRIPT_NAME@@", application_path) >=
                 (int)sizeof(application_script)) {
        fprintf(stderr, "Application bundle path is too long.\n");
        return 1;
    }

    if (setenv("PYTHONHOME", python_home, 1) != 0 ||
        setenv("PYTHONPATH", application_path, 1) != 0 ||
        setenv("PYTHONNOUSERSITE", "1", 1) != 0) {
        perror("Could not configure the bundled Python runtime");
        return 1;
    }

    char **python_argv = calloc((size_t)argc + 2, sizeof(char *));
    if (!python_argv) {
        perror("Could not allocate interpreter arguments");
        return 1;
    }
    python_argv[0] = python_executable;
    python_argv[1] = application_script;
    for (int index = 1; index < argc; ++index) {
        python_argv[index + 1] = argv[index];
    }

    execv(python_executable, python_argv);
    perror("Could not start the bundled Python interpreter");
    free(python_argv);
    return 1;
}
"""


def _run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildError(f"Could not run {command[0]}: {error}") from error


def _require_macos_arm64() -> None:
    if sys.platform != "darwin" or platform.system() != "Darwin":
        raise BuildError("Building a native macOS .app requires macOS.")
    if platform.machine() not in {"arm64", "aarch64"}:
        raise BuildError("Run the macOS ARM64 build on an Apple Silicon Mac.")


def _toolchain() -> tuple[str, str, str]:
    xcrun = shutil.which("xcrun")
    if not xcrun:
        raise BuildError("xcrun is required; install Xcode command-line tools.")

    sdk_result = _run([xcrun, "--sdk", "macosx", "--show-sdk-path"])
    sdk = sdk_result.stdout.strip()
    if sdk_result.returncode != 0 or not sdk or not Path(sdk).is_dir():
        details = sdk_result.stderr.strip() or "macOS SDK was not found."
        raise BuildError(f"Could not locate the macOS SDK: {details}")

    clang_result = _run([xcrun, "--find", "clang"])
    clang = clang_result.stdout.strip()
    if clang_result.returncode != 0 or not clang or not Path(clang).is_file():
        details = clang_result.stderr.strip() or "clang was not found."
        raise BuildError(f"Could not locate Xcode clang: {details}")

    lipo_result = _run([xcrun, "--find", "lipo"])
    lipo = lipo_result.stdout.strip()
    if lipo_result.returncode != 0 or not lipo or not Path(lipo).is_file():
        details = lipo_result.stderr.strip() or "lipo was not found."
        raise BuildError(f"Could not locate Xcode lipo: {details}")
    return sdk, clang, lipo


def _framework_version(framework: Path) -> tuple[Path, str, Path]:
    framework = framework.resolve()
    if framework.name != "Python.framework" or not framework.is_dir():
        raise BuildError(
            "--python-framework must point to a Python.framework directory."
        )

    versions_directory = framework / "Versions"
    versions = sorted(
        (path for path in versions_directory.iterdir()
         if path.is_dir() and re.fullmatch(r"\d+\.\d+", path.name)),
        key=lambda path: tuple(int(part) for part in path.name.split(".")),
        reverse=True,
    ) if versions_directory.is_dir() else []
    for version_directory in versions:
        version = version_directory.name
        interpreter = version_directory / "bin" / "python3"
        standard_library = version_directory / "lib" / f"python{version}"
        if interpreter.is_file() and standard_library.is_dir():
            return framework, version, interpreter
    raise BuildError(
        "Python.framework must contain Versions/<major.minor>/bin/python3 and "
        "Versions/<major.minor>/lib/python<major.minor>."
    )


def _ensure_arm64(lipo: str, interpreter: Path) -> None:
    result = _run([lipo, "-verify_arch", "arm64", str(interpreter)])
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise BuildError(
            f"The selected CPython interpreter does not contain arm64: "
            f"{interpreter}" + (f" ({details})" if details else "")
        )


def _validate_framework_architecture(lipo: str, framework: Path) -> None:
    framework = framework.resolve()
    paths = list(framework.rglob("*"))
    for path in paths:
        if not path.is_symlink():
            continue
        try:
            target = path.resolve(strict=True)
            target.relative_to(framework)
        except (OSError, ValueError) as error:
            raise BuildError(
                f"Python.framework contains a broken or external symbolic link: {path}"
            ) from error

    candidates = [
        path for path in paths
        if path.is_file()
        and (
            path.suffix.lower() in {".dylib", ".so", ".bundle"}
            or path.name == "Python"
        )
    ]
    for candidate in candidates:
        _ensure_arm64(lipo, candidate)


def _safe_include_files(
    project_root: Path, source: Path, include_paths: list[Path]
) -> dict[Path, Path]:
    included: dict[Path, Path] = {}
    for requested in include_paths:
        candidate = requested if requested.is_absolute() else project_root / requested
        if candidate.is_symlink():
            raise BuildError(f"Symbolic links are not supported in includes: {candidate}")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(project_root)
        except (OSError, ValueError) as error:
            raise BuildError(
                f"Included paths must exist inside the source directory: {requested}"
            ) from error
        paths = sorted(resolved.rglob("*")) if resolved.is_dir() else [resolved]
        for path in paths:
            if path.is_symlink():
                raise BuildError(f"Symbolic links are not supported in includes: {path}")
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            try:
                relative = path.relative_to(project_root)
            except ValueError as error:
                raise BuildError(f"Included path escapes the source directory: {path}") from error
            if path.resolve() == source:
                continue
            if relative.as_posix() == "__main__.py":
                raise BuildError("An included file cannot overwrite the bundled entry point.")
            if relative in included and included[relative] != path:
                raise BuildError(f"Duplicate included destination: {relative}")
            included[relative] = path
    return included


def _launcher_source(version: str) -> str:
    return LAUNCHER_SOURCE.replace("@@PYTHON_VERSION@@", version).replace(
        "@@SCRIPT_NAME@@", "__main__.py"
    )


def build_app(
    source: Path,
    output: Path,
    python_framework: Path,
    bundle_id: str,
    includes: list[Path] | None = None,
) -> None:
    _require_macos_arm64()
    sdk, clang, lipo = _toolchain()

    source = source.resolve()
    output = output.resolve()
    if not source.is_file() or source.suffix.lower() != ".py":
        raise BuildError(f"Source must be an existing .py file: {source}")
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle_id):
        raise BuildError("Bundle ID must be a reverse-DNS identifier such as org.example.App.")
    app_name = output.name[:-4] if output.name.endswith(".app") else ""
    if not app_name or app_name in {".", ".."} or any(
        character in app_name for character in '<>:"/\\|?*'
    ):
        raise BuildError("Output must be a named .app bundle.")
    if output.exists():
        raise BuildError(f"Refusing to overwrite existing output: {output}")

    framework, version, interpreter = _framework_version(python_framework)
    _ensure_arm64(lipo, interpreter)
    _validate_framework_architecture(lipo, framework)
    project_root = source.parent
    if output == project_root or project_root in output.parents:
        raise BuildError("Place the .app output outside the Python source directory.")
    included_files = _safe_include_files(project_root, source, includes or [])

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".macos-arm64-", dir=output.parent))
    temporary_app = temporary_root / output.name
    try:
        contents = temporary_app / "Contents"
        macos = contents / "MacOS"
        app_resources = contents / "Resources" / "app"
        frameworks = contents / "Frameworks"
        macos.mkdir(parents=True)
        app_resources.mkdir(parents=True)
        frameworks.mkdir()

        shutil.copytree(framework, frameworks / "Python.framework", symlinks=True)
        shutil.copy2(source, app_resources / "__main__.py")
        for relative, included_source in included_files.items():
            destination = app_resources / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(included_source, destination)

        launcher = macos / app_name
        launcher_source = temporary_root / "launcher.c"
        launcher_source.write_text(_launcher_source(version), encoding="utf-8")
        completed = _run([
            clang,
            "-arch", "arm64",
            "-isysroot", sdk,
            "-mmacosx-version-min=11.0",
            "-O2",
            "-o", str(launcher),
            str(launcher_source),
        ], timeout=120)
        if completed.returncode != 0:
            details = (completed.stdout + completed.stderr).strip()
            raise BuildError(f"Compiling the native ARM64 launcher failed:\n{details}")
        if not launcher.is_file():
            raise BuildError("clang finished without producing the app launcher.")
        launcher.chmod(0o755)

        info = {
            "CFBundleDevelopmentRegion": "en",
            "CFBundleExecutable": app_name,
            "CFBundleIdentifier": bundle_id,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": app_name,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        }
        with (contents / "Info.plist").open("wb") as plist_file:
            plistlib.dump(info, plist_file, sort_keys=True)
        os.rename(temporary_app, output)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a native macOS ARM64 .app using an installed CPython.framework."
    )
    parser.add_argument("source", type=Path, help="Python entry-point file")
    parser.add_argument("output", type=Path, help="Output .app path (must not exist)")
    parser.add_argument(
        "--python-framework", type=Path, required=True,
        help="Python.framework, for example /Library/Frameworks/Python.framework",
    )
    parser.add_argument(
        "--bundle-id", required=True,
        help="Unique reverse-DNS bundle identifier, for example org.example.MyApp",
    )
    parser.add_argument(
        "--include", action="append", default=[], type=Path,
        help="File or directory to include from the entry point's directory (repeatable)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        build_app(
            args.source, args.output, args.python_framework, args.bundle_id, args.include
        )
    except BuildError as error:
        print(f"macOS ARM64 build failed: {error}", file=sys.stderr)
        return 1
    print(f"Built unsigned macOS ARM64 application: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
