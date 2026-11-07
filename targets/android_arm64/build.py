"""Build a Python script as an Android arm64-v8a APK from a CPython Android prefix."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Mapping


ROOT = Path(__file__).resolve().parent
PROJECT_TEMPLATE = ROOT / "android_project"
NDK_VERSION = "27.3.13750724"
ANDROID_PLATFORM = "android-35"
ANDROID_CMAKE = "3.22.1"
MIN_GRADLE = (8, 11, 1)
APP_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


class AndroidBuildError(Exception):
    """Raised when an Android APK cannot be built with the available inputs."""


def _find_executable(name: str, which: Callable[[str], str | None]) -> str | None:
    return which(name) or which(name + ".bat") or which(name + ".cmd")


def _command_for_executable(
    executable: str,
    args: list[str],
    windows: bool = os.name == "nt",
    command_processor: str | None = None,
) -> list[str]:
    if windows and Path(executable).suffix.lower() in {".bat", ".cmd"}:
        return [
            command_processor or os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            executable,
            *args,
        ]
    return [executable, *args]


def _version_tuple(text: str, pattern: str) -> tuple[int, ...] | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _check_command_version(
    executable: str | None,
    args: list[str],
    version_pattern: str,
    minimum: tuple[int, ...],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[bool, str]:
    if executable is None:
        return False, "Not found on PATH."
    try:
        result = runner(
            _command_for_executable(executable, args),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return False, f"Could not run {executable}: {error}"
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    version = _version_tuple(output, version_pattern)
    if result.returncode != 0 or version is None:
        return False, f"Could not determine version using {executable}."
    if version < minimum:
        return False, f"Found {'.'.join(map(str, version))}; need at least {'.'.join(map(str, minimum))}."
    return True, f"Found version {'.'.join(map(str, version))}."


def _python_prefix_status(prefix: Path | None) -> tuple[bool, str]:
    if prefix is None or not prefix.is_dir():
        return False, "Pass --python-prefix or set PYTHON_ANDROID_PREFIX to a CPython Android prefix."

    lib_dir = prefix / "lib"
    versions = sorted(
        (
            match.group(1)
            for path in lib_dir.glob("python*")
            if path.is_dir()
            for match in [re.fullmatch(r"python(\d+\.\d+[a-z]*)", path.name)]
            if match
        ),
        key=lambda value: tuple(int(part) for part in re.findall(r"\d+", value)[:2]),
    )
    if len(versions) != 1:
        return False, f"Expected exactly one lib/pythonX.Y runtime directory under {lib_dir}."
    version = versions[0]
    include_dir = prefix / "include" / f"python{version}"
    runtime_dir = lib_dir / f"python{version}"
    python_library = lib_dir / f"libpython{version}.so"
    if not (include_dir / "Python.h").is_file():
        return False, f"Missing CPython Android headers: {include_dir / 'Python.h'}."
    if not python_library.is_file():
        return False, f"Missing shared CPython runtime: {python_library}."
    if not (runtime_dir / "encodings" / "__init__.py").is_file():
        return False, f"Missing CPython bootstrap module: {runtime_dir / 'encodings' / '__init__.py'}."
    sysconfig = list(runtime_dir.glob("_sysconfigdata_*_android_*.py"))
    if not any(path.name.endswith("_aarch64-linux-android.py") for path in sysconfig):
        return False, "Prefix does not identify the aarch64-linux-android ABI in sysconfigdata."
    if not list(lib_dir.glob("lib*_python.so")):
        return False, f"Missing CPython Android dependency libraries in {lib_dir}."
    return True, f"Found CPython {version} Android prefix for arm64-v8a."


def inspect_requirements(
    python_prefix: Path | None = None,
    release: bool = False,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, dict[str, object]]:
    """Check all build inputs without installing tools or invoking the SDK manager."""
    env = os.environ if environment is None else environment
    sdk_value = env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT")
    sdk = Path(sdk_value).expanduser() if sdk_value else None
    sdk_valid = sdk is not None and sdk.is_dir()
    ndk = sdk / "ndk" / NDK_VERSION if sdk_valid else None
    java = _find_executable("java", which)
    gradle = _find_executable("gradle", which)

    java_ok, java_detail = _check_command_version(
        java, ["-version"], r'version\s+"?(\d+(?:\.\d+){0,2})', (17,), runner
    )
    gradle_ok, gradle_detail = _check_command_version(
        gradle, ["--version"], r"Gradle\s+(\d+(?:\.\d+){0,2})", MIN_GRADLE, runner
    )
    prefix_ok, prefix_detail = _python_prefix_status(python_prefix)
    requirements: dict[str, dict[str, object]] = {
        "android_sdk": {
            "required": True,
            "detected": bool(sdk_valid and (sdk / "platforms" / ANDROID_PLATFORM).is_dir()),
            "detail": (
                f"Found {sdk / 'platforms' / ANDROID_PLATFORM}."
                if sdk_valid and (sdk / "platforms" / ANDROID_PLATFORM).is_dir()
                else f"Install {ANDROID_PLATFORM} in the Android SDK and set ANDROID_HOME or ANDROID_SDK_ROOT."
            ),
        },
        "android_ndk": {
            "required": True,
            "detected": bool(ndk and (ndk / "package.xml").is_file()),
            "detail": (
                f"Found NDK {NDK_VERSION}."
                if ndk and (ndk / "package.xml").is_file()
                else f"Install NDK {NDK_VERSION} under the Android SDK."
            ),
        },
        "android_cmake": {
            "required": True,
            "detected": bool(
                sdk_valid
                and (sdk / "cmake" / ANDROID_CMAKE / "bin" / (
                    "cmake.exe" if os.name == "nt" else "cmake"
                )).is_file()
            ),
            "detail": (
                f"Found CMake {ANDROID_CMAKE}."
                if sdk_valid
                and (sdk / "cmake" / ANDROID_CMAKE / "bin" / (
                    "cmake.exe" if os.name == "nt" else "cmake"
                )).is_file()
                else f"Install CMake {ANDROID_CMAKE} from the Android SDK."
            ),
        },
        "java": {"required": True, "detected": java_ok, "detail": java_detail},
        "gradle": {"required": True, "detected": gradle_ok, "detail": gradle_detail},
        "cpython_android_prefix": {
            "required": True,
            "detected": prefix_ok,
            "detail": prefix_detail,
        },
    }

    if release:
        keystore_value = env.get("ANDROID_SIGNING_KEYSTORE")
        key_fields = (
            keystore_value,
            env.get("ANDROID_SIGNING_KEY_ALIAS"),
            env.get("ANDROID_SIGNING_STORE_PASSWORD"),
            env.get("ANDROID_SIGNING_KEY_PASSWORD"),
        )
        keystore_exists = bool(keystore_value and Path(keystore_value).expanduser().is_file())
        release_signing_ok = keystore_exists and all(key_fields[1:])
        requirements["release_signing"] = {
            "required": True,
            "detected": release_signing_ok,
            "detail": (
                "Signing keystore and credentials found in the environment."
                if release_signing_ok
                else "Set ANDROID_SIGNING_KEYSTORE, ANDROID_SIGNING_KEY_ALIAS, "
                "ANDROID_SIGNING_STORE_PASSWORD, and ANDROID_SIGNING_KEY_PASSWORD."
            ),
        }
    return requirements


def _check_app_id(application_id: str) -> None:
    if not APP_ID_PATTERN.fullmatch(application_id):
        raise AndroidBuildError(
            "Application ID must contain at least two dot-separated Android package-name segments."
        )


def _missing_requirements(
    requirements: Mapping[str, Mapping[str, object]],
) -> list[str]:
    return [
        name for name, details in requirements.items()
        if details["required"] and not details["detected"]
    ]


def build_apk(
    source: Path,
    output: Path,
    python_prefix: Path,
    application_id: str = "org.pypack.android",
    release: bool = False,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Build and atomically copy a signed/debug APK using the local Gradle toolchain."""
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    python_prefix = python_prefix.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".py":
        raise AndroidBuildError(f"Source must be an existing .py file: {source}")
    if output.suffix.lower() != ".apk":
        raise AndroidBuildError(f"Output must use the .apk extension: {output}")
    if source == output:
        raise AndroidBuildError("The APK output cannot overwrite the Python source.")
    if not PROJECT_TEMPLATE.is_dir():
        raise AndroidBuildError(f"Android project scaffold is missing: {PROJECT_TEMPLATE}")
    _check_app_id(application_id)

    requirements = inspect_requirements(
        python_prefix, release, environment=environment, which=which, runner=runner
    )
    missing = _missing_requirements(requirements)
    if missing:
        details = "; ".join(
            f"{name}: {requirements[name]['detail']}" for name in missing
        )
        raise AndroidBuildError(f"Android APK build is blocked ({details})")

    gradle = _find_executable("gradle", which)
    if gradle is None:
        raise AndroidBuildError("Gradle was not found on PATH.")
    env = dict(os.environ if environment is None else environment)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="android-arm64-") as temporary:
        project = Path(temporary) / "project"
        shutil.copytree(PROJECT_TEMPLATE, project)
        task = "assembleRelease" if release else "assembleDebug"
        command = [
            gradle,
            "--no-daemon",
            "-p",
            str(project),
            f"-PpythonPrefix={python_prefix}",
            f"-PpythonEntry={source}",
            f"-PapplicationId={application_id}",
            task,
        ]
        result = runner(
            _command_for_executable(gradle, command[1:]),
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise AndroidBuildError(f"Gradle {task} failed with exit code {result.returncode}.")

        variant = "release" if release else "debug"
        artifacts = list((project / "app" / "build" / "outputs" / "apk" / variant).glob("*.apk"))
        if len(artifacts) != 1 or not artifacts[0].is_file():
            raise AndroidBuildError("Gradle completed without producing exactly one APK.")
        temporary_output = tempfile.NamedTemporaryFile(
            prefix=output.name + ".", suffix=".tmp", dir=output.parent, delete=False
        )
        temporary_output.close()
        staged_output = Path(temporary_output.name)
        try:
            shutil.copy2(artifacts[0], staged_output)
            os.replace(staged_output, output)
        finally:
            staged_output.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a Python script as an Android arm64-v8a APK."
    )
    parser.add_argument("source", type=Path, nargs="?", help="Python .py entry script")
    parser.add_argument("output", type=Path, nargs="?", help="output .apk path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report APK build prerequisites without starting a build",
    )
    parser.add_argument(
        "--python-prefix",
        type=Path,
        default=Path(os.environ["PYTHON_ANDROID_PREFIX"])
        if os.environ.get("PYTHON_ANDROID_PREFIX") else None,
        help="CPython Android prefix for aarch64-linux-android",
    )
    parser.add_argument(
        "--application-id",
        default="org.pypack.android",
        help="Android application ID (default: org.pypack.android)",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="create a release APK using signing credentials from environment variables",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.check:
        requirements = inspect_requirements(args.python_prefix, args.release)
        missing = _missing_requirements(requirements)
        for name, details in requirements.items():
            state = "OK" if details["detected"] else "MISSING"
            print(f"[{state}] {name}: {details['detail']}")
        return 1 if missing else 0
    if args.source is None:
        parser.error("source is required unless --check is used.")
    if args.python_prefix is None:
        parser.error("--python-prefix is required unless PYTHON_ANDROID_PREFIX is set.")
    output = args.output or args.source.with_suffix(".apk")
    try:
        build_apk(
            args.source,
            output,
            args.python_prefix,
            args.application_id,
            args.release,
        )
    except AndroidBuildError as error:
        parser.error(str(error))
    print(f"Created {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
