"""Capability probe for the Linux ARM64 package target."""

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


TARGET = {
    "id": "linux_arm64",
    "os": "linux",
    "architecture": "aarch64",
    "compiler_triplet_prefix": "aarch64-linux-",
    "executable_format": "ELF",
}

# This repository's current launcher and package builder are Windows-only.
LINUX_EXECUTABLE_BACKEND_IMPLEMENTED = False


@dataclass(frozen=True)
class TargetConfig:
    compiler: str | None
    compiler_triplet: str | None
    compiler_probe_error: str | None
    sysroot: Path | None
    python_root: Path | None
    readelf: str | None
    python_machine: str | None
    runtime_probe_error: str | None


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )


def discover_config(
    environ: Mapping[str, str] | None = None,
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
) -> TargetConfig:
    """Resolve configured host tools and inspect target compiler/runtime metadata."""
    environ = os.environ if environ is None else environ

    compiler_name = environ.get("LINUX_ARM64_CC", "aarch64-linux-gnu-gcc").strip()
    compiler = which(compiler_name) if compiler_name else None
    compiler_triplet = None
    compiler_probe_error = None
    if compiler:
        try:
            result = run([compiler, "-dumpmachine"])
            if result.returncode == 0:
                compiler_triplet = result.stdout.strip()
            else:
                compiler_probe_error = result.stderr.strip() or f"Compiler probe exited with {result.returncode}."
        except (OSError, subprocess.SubprocessError) as error:
            compiler_probe_error = str(error)

    readelf = which("readelf")
    python_root_value = environ.get("LINUX_ARM64_PYTHON_ROOT", "").strip()
    python_root = Path(python_root_value).expanduser() if python_root_value else None
    python_machine = None
    runtime_probe_error = None
    interpreter = python_root / "bin" / "python3" if python_root else None
    if readelf and interpreter and interpreter.is_file():
        try:
            result = run([readelf, "-h", str(interpreter)])
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.strip().startswith("Machine:"):
                        python_machine = line.split(":", 1)[1].strip()
                        break
                if python_machine is None:
                    runtime_probe_error = "readelf output did not contain an ELF Machine field."
            else:
                runtime_probe_error = result.stderr.strip() or f"readelf exited with {result.returncode}."
        except (OSError, subprocess.SubprocessError) as error:
            runtime_probe_error = str(error)

    sysroot_value = environ.get("LINUX_ARM64_SYSROOT", "").strip()
    sysroot = Path(sysroot_value).expanduser() if sysroot_value else None
    return TargetConfig(
        compiler=compiler,
        compiler_triplet=compiler_triplet,
        compiler_probe_error=compiler_probe_error,
        sysroot=sysroot,
        python_root=python_root,
        readelf=readelf,
        python_machine=python_machine,
        runtime_probe_error=runtime_probe_error,
    )


def describe_target(
    config: TargetConfig,
    *,
    host_system: str | None = None,
    host_machine: str | None = None,
) -> dict[str, object]:
    """Describe readiness without treating configured paths as a successful build."""
    host = {
        "system": host_system or platform.system().lower(),
        "machine": host_machine or platform.machine().lower(),
    }

    compiler_ok = bool(
        config.compiler
        and config.compiler_triplet
        and config.compiler_triplet.startswith(TARGET["compiler_triplet_prefix"])
    )
    sysroot_ok = bool(config.sysroot and config.sysroot.is_dir())
    runtime_issues = []
    if config.python_root is None:
        runtime_issues.append("LINUX_ARM64_PYTHON_ROOT is not set.")
    elif not config.python_root.is_dir():
        runtime_issues.append(f"CPython runtime root does not exist: {config.python_root}")
    else:
        interpreter = config.python_root / "bin" / "python3"
        include_dir, python_library = _python_link_inputs(config.python_root)
        if not interpreter.is_file():
            runtime_issues.append(f"CPython interpreter is missing: {interpreter}")
        if include_dir is None:
            runtime_issues.append(
                f"CPython headers (python*/Python.h) are missing under {config.python_root / 'include'}."
            )
        if python_library is None:
            runtime_issues.append(
                f"CPython shared library (libpython*.so*) is missing under "
                f"{config.python_root / 'lib'} or {config.python_root / 'lib64'}."
            )
        if config.readelf is None:
            runtime_issues.append("Cannot verify the target interpreter architecture because readelf is unavailable.")
        elif config.runtime_probe_error:
            runtime_issues.append(config.runtime_probe_error)
        elif config.python_machine != "AArch64":
            runtime_issues.append(
                f"Target interpreter must be an AArch64 ELF binary; detected {config.python_machine or 'unknown'}."
            )
    runtime_architecture_ok = config.python_machine == "AArch64"
    runtime_ok = not runtime_issues and runtime_architecture_ok

    requirements = [
        {
            "name": "aarch64-linux cross compiler",
            "available": compiler_ok,
            "detail": (
                config.compiler_triplet
                if compiler_ok
                else config.compiler_probe_error
                or "No aarch64-linux cross compiler found (default: aarch64-linux-gnu-gcc); "
                "set LINUX_ARM64_CC to its executable."
            ),
        },
        {
            "name": "Linux ARM64 sysroot",
            "available": sysroot_ok,
            "detail": (
                str(config.sysroot)
                if sysroot_ok
                else f"Configured sysroot does not exist: {config.sysroot}"
                if config.sysroot
                else "LINUX_ARM64_SYSROOT is not set."
            ),
        },
        {
            "name": "Linux ARM64 CPython runtime",
            "available": runtime_ok,
            "detail": (
                f"ELF machine: {config.python_machine}"
                if runtime_ok
                else " ".join(runtime_issues)
            ),
        },
        {
            "name": "ELF inspection tool",
            "available": config.readelf is not None,
            "detail": config.readelf or "readelf is not available on PATH; install it or add it to PATH.",
        },
        {
            "name": "Linux executable backend",
            "available": LINUX_EXECUTABLE_BACKEND_IMPLEMENTED,
            "detail": (
                "Implemented."
                if LINUX_EXECUTABLE_BACKEND_IMPLEMENTED
                else "Not implemented; the existing launcher and packager are Windows-only."
            ),
        },
    ]
    blockers = [item["detail"] for item in requirements if not item["available"]]
    return {
        "target": TARGET,
        "host": host,
        "build_available": not blockers,
        "build_command": None,
        "requirements": requirements,
        "blockers": blockers,
    }


def _python_link_inputs(python_root: Path) -> tuple[Path | None, Path | None]:
    include_root = python_root / "include"
    include_dirs = sorted(
        path.parent
        for path in include_root.glob("python*/Python.h")
        if path.is_file()
    )
    library_roots = (python_root / "lib", python_root / "lib64")
    libraries = sorted(
        path
        for library_root in library_roots
        if library_root.is_dir()
        for path in library_root.glob("libpython*.so*")
        if path.is_file()
    )
    libraries.sort(key=lambda path: (not path.name.endswith(".so"), path.name))
    return (
        include_dirs[0] if include_dirs else None,
        libraries[0] if libraries else None,
    )


def build_recipe(
    config: TargetConfig,
    launcher_source: Path,
    output: Path,
    *,
    host_system: str | None = None,
) -> dict[str, object]:
    """Prepare, but never execute, a Linux ARM64 CPython launcher compile recipe."""
    host = host_system or platform.system().lower()
    launcher_source = launcher_source.expanduser().resolve()
    output = output.expanduser().resolve()
    include_dir, python_library = (
        _python_link_inputs(config.python_root)
        if config.python_root and config.python_root.is_dir()
        else (None, None)
    )
    report = describe_target(config, host_system=host)
    blockers = [
        item["detail"]
        for item in report["requirements"]
        if item["name"] != "Linux executable backend" and not item["available"]
    ]
    if host != "linux":
        blockers.append("Run the recipe on a Linux build host; this adapter does not invoke Windows cross-build tools.")
    if not launcher_source.is_file():
        blockers.append(f"Linux launcher C source does not exist: {launcher_source}")
    if include_dir is None:
        blockers.append("CPython headers were not found under LINUX_ARM64_PYTHON_ROOT/include.")
    if python_library is None:
        blockers.append("A CPython shared library (libpython*.so*) was not found under the configured runtime.")
    if output == launcher_source:
        blockers.append("The launcher output cannot overwrite its C source.")

    compile_argv = None
    shell_command = None
    commands = []
    recipe_available = not blockers
    if recipe_available:
        compile_argv = [
            config.compiler,
            f"--sysroot={config.sysroot}",
            "-fPIE",
            "-pie",
            f"-I{include_dir}",
            str(launcher_source),
            f"-L{python_library.parent}",
            f"-l:{python_library.name}",
            "-ldl",
            "-lm",
            "-o",
            str(output),
        ]
        shell_command = shlex.join(compile_argv)
        commands = [
            ["mkdir", "-p", str(output.parent)],
            compile_argv,
        ]

    return {
        "target": TARGET,
        "host": {"system": host, "machine": platform.machine().lower()},
        "recipe_available": recipe_available,
        "recipe_scope": "Compile a supplied C launcher against the target CPython shared library; this does not package an application.",
        "commands": commands,
        "compile_argv": compile_argv,
        "shell_command": shell_command,
        "application_build_available": False,
        "application_build_blocker": (
            "The Linux application bundling/launcher backend is not implemented."
        ),
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or prepare a Linux ARM64 compile recipe.")
    parser.add_argument("--launcher-source", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if (arguments.launcher_source is None) != (arguments.output is None):
        parser.error("--launcher-source and --output must be supplied together")
    config = discover_config()
    result = (
        build_recipe(config, arguments.launcher_source, arguments.output)
        if arguments.launcher_source is not None
        else describe_target(config)
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
