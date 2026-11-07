"""Capability and local-prerequisite checks for the Android ARM64 target."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from .build import inspect_requirements as _inspect_build_requirements


TARGET = {
    "id": "android_arm64",
    "platform": "Android",
    "abi": "arm64-v8a",
    "artifact": "APK",
    "artifact_is_generic_executable": False,
    "supports_windows_exe_conversion": False,
    "python_apk_backend_implemented": True,
}


def inspect_requirements(
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    python_prefix: Path | None = None,
    release: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, dict[str, object]]:
    """Report toolchain and Android CPython prefix readiness without installing anything."""
    return _inspect_build_requirements(
        python_prefix, release, environment=environment, which=which, runner=runner
    )


def get_build_decision(
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    python_prefix: Path | None = None,
    release: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    """Fail closed unless every prerequisite for the requested APK variant is present."""
    requirements = inspect_requirements(
        environment, which, python_prefix, release, runner
    )
    missing = [
        requirement for requirement, details in requirements.items()
        if details["required"] and not details["detected"]
    ]
    if missing:
        reasons = [
            "Missing prerequisites: " + ", ".join(missing) + ".",
        ]
    else:
        reasons = []
    return {
        "can_build_apk": not missing,
        "target": dict(TARGET),
        "requirements": requirements,
        "reasons": reasons,
    }
