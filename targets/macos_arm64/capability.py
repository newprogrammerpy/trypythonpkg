"""Report whether this checkout can build the macOS ARM64 target."""

import json
import platform
import shutil
import subprocess
from pathlib import Path


TARGET_PATH = Path(__file__).with_name("target.json")


def _find_macos_sdk() -> tuple[str | None, str | None]:
    xcrun = shutil.which("xcrun")
    if xcrun is None:
        return None, "xcrun was not found; install Xcode command-line tools."

    try:
        result = subprocess.run(
            [xcrun, "--sdk", "macosx", "--show-sdk-path"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None, "xcrun timed out while locating the macOS SDK."

    sdk_path = result.stdout.strip()
    if result.returncode == 0 and sdk_path:
        return sdk_path, None
    details = result.stderr.strip() or "xcrun did not return a macOS SDK path."
    return None, details


def get_capability(
    system: str | None = None,
    machine: str | None = None,
    sdk_path: str | None = None,
    sdk_error: str | None = None,
    native_tools: dict[str, str | None] | None = None,
) -> dict:
    target = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
    system = system or platform.system()
    machine = machine or platform.machine()
    is_macos = system == "Darwin"

    if is_macos and sdk_path is None and sdk_error is None:
        sdk_path, sdk_error = _find_macos_sdk()

    tool_paths = native_tools or {
        name: shutil.which(name) if is_macos else None
        for name in ("clang", "lipo")
    }
    host_ready = (
        is_macos
        and sdk_path is not None
        and Path(sdk_path).is_dir()
        and all(tool_paths.values())
        and machine in {"arm64", "aarch64"}
    )
    blockers = []
    if not is_macos:
        blockers.append(f"A macOS build host is required; current host is {system}.")
    elif sdk_path is None or not Path(sdk_path).is_dir():
        blockers.append(sdk_error or "The macOS SDK is unavailable.")
    elif not all(tool_paths.values()):
        blockers.append("Xcode clang and lipo are required to build and verify the ARM64 app.")
    if is_macos and machine not in {"arm64", "aarch64"}:
        blockers.append(f"A native Apple Silicon build host is required; current architecture is {machine}.")
    blockers.append(
        "Pass an arm64-capable Python.framework to build_app.py; this probe does not inspect a runtime."
    )

    return {
        "target": target["id"],
        "artifact": target["artifact"],
        "host": {"os": system, "architecture": machine},
        "macos_sdk": {
            "checked": is_macos,
            "available": sdk_path is not None,
            "path": sdk_path,
            "error": sdk_error,
        },
        "native_tools": tool_paths,
        "host_ready": host_ready,
        "builder_available": True,
        "signing": {
            "status": "not_checked",
            "distribution_requirements": target["distribution"],
        },
        "blockers": blockers,
    }


if __name__ == "__main__":
    print(json.dumps(get_capability(), indent=2))
