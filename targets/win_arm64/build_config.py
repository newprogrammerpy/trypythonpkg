"""Preflight MSVC and CPython prerequisites for a Windows ARM64 smoke build."""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


TARGET = "win_arm64"
ARM64_COFF_MACHINE = 0xAA64
ARM64_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.ARM64"
INSTALL_GUIDANCE = (
    "In Visual Studio Installer, modify the Build Tools installation and add the "
    f"C++ ARM64 build tools component ({ARM64_COMPONENT}) under Individual components. "
    "Add the Windows 11 SDK component "
    "(Microsoft.VisualStudio.Component.Windows11SDK.26100) for executable linking."
)
PYTHON_GUIDANCE = (
    "Install a CPython Windows ARM64 build from python.org and make it discoverable "
    "through the Python launcher (py -0p), then rerun this preflight."
)


def _coff_machine(object_bytes: bytes) -> int:
    if len(object_bytes) < 2:
        raise ValueError("Invalid or empty COFF object.")
    return struct.unpack_from("<H", object_bytes)[0]


def _pe_machine(path: Path) -> int | None:
    with path.open("rb") as binary:
        header = binary.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            return None
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        binary.seek(pe_offset)
        pe_header = binary.read(6)
    if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", pe_header, 4)[0]


def _machine_name(machine: int) -> str:
    names = {
        ARM64_COFF_MACHINE: "ARM64 (0xAA64)",
        0x8664: "AMD64 (0x8664)",
        0x014C: "x86 (0x014C)",
    }
    return names.get(machine, f"0x{machine:04X}")


@dataclass(frozen=True)
class TargetStatus:
    target: str
    supported: bool
    compiler: str | None
    object_machine: str | None
    executable_built: bool
    executable_machine: str | None
    python_executable: str | None
    python_machine: str | None
    python_runtime_found: bool
    reason: str


def _vswhere_paths() -> list[Path]:
    candidates = (
        Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"),
        Path(r"C:\Program Files\Microsoft Visual Studio\Installer\vswhere.exe"),
    )
    return [path for path in candidates if path.is_file()]


def _visual_studio_roots() -> list[Path]:
    roots: list[Path] = []
    for variable in ("VSINSTALLDIR", "VS170COMNTOOLS", "VS160COMNTOOLS"):
        value = os.environ.get(variable)
        if value:
            root = Path(value)
            if root.name.lower() == "common7":
                root = root.parent
            elif root.name.lower() == "tools":
                root = root.parent.parent
            if root not in roots:
                roots.append(root)

    for vswhere in _vswhere_paths():
        result = subprocess.run(
            [str(vswhere), "-all", "-products", "*", "-property", "installationPath"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            for value in result.stdout.splitlines():
                root = Path(value.strip())
                if value.strip() and root not in roots:
                    roots.append(root)
    return roots


def _compiler_candidates() -> list[Path | None]:
    candidates: list[Path | None] = []
    for root in _visual_studio_roots():
        vcvarsall = root / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
        arm64_compilers = (root / "VC" / "Tools" / "MSVC").glob(
            "*/bin/Host*/arm64/cl.exe"
        )
        if (
            vcvarsall.is_file()
            and any(path.is_file() for path in arm64_compilers)
            and vcvarsall not in candidates
        ):
            candidates.append(vcvarsall)
    if not candidates and shutil.which("cl.exe"):
        candidates.append(None)
    return candidates


def _python_candidates() -> list[Path]:
    candidates: list[Path] = []
    if getattr(sys, "implementation", None) is not None:
        if sys.implementation.name == "cpython":
            candidates.append(Path(sys.executable))

    launcher = shutil.which("py.exe") or shutil.which("py")
    if launcher:
        try:
            result = subprocess.run(
                [launcher, "-0p"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            for line in result.stdout.splitlines():
                match = re.search(
                    r"([A-Za-z]:\\.*?pythonw?\.exe)\s*$", line, re.IGNORECASE
                )
                if match:
                    candidates.append(Path(match.group(1)))

    path_python = shutil.which("python.exe")
    if path_python:
        candidates.append(Path(path_python))

    unique: dict[str, Path] = {}
    for candidate in candidates:
        if candidate.is_file():
            unique.setdefault(str(candidate).casefold(), candidate)
    return list(unique.values())


def _find_cpython_arm64() -> tuple[Path | None, str | None, bool, str]:
    detected_interpreters: list[tuple[Path, int]] = []
    arm64_interpreter_without_runtime: tuple[Path, int] | None = None
    for executable in _python_candidates():
        try:
            machine = _pe_machine(executable)
        except OSError:
            continue
        if machine is None:
            continue
        detected_interpreters.append((executable, machine))
        if machine != ARM64_COFF_MACHINE:
            continue

        runtime_found = False
        for dll in executable.parent.glob("python*.dll"):
            if not dll.is_file():
                continue
            try:
                if _pe_machine(dll) == ARM64_COFF_MACHINE:
                    runtime_found = True
                    break
            except OSError:
                continue
        if runtime_found:
            return executable, _machine_name(machine), True, ""
        arm64_interpreter_without_runtime = (executable, machine)

    if arm64_interpreter_without_runtime:
        executable, machine = arm64_interpreter_without_runtime
        reason = "An ARM64 Python executable was found, but no ARM64 CPython DLL was found beside it."
        return executable, _machine_name(machine), False, reason
    elif detected_interpreters:
        found = ", ".join(
            sorted({_machine_name(machine) for _, machine in detected_interpreters})
        )
        executable, machine = detected_interpreters[0]
        reason = (
            "No CPython ARM64 runtime was found; detected Python machine(s): "
            f"{found}. {PYTHON_GUIDANCE}"
        )
        return executable, _machine_name(machine), False, reason
    else:
        reason = (
            "No CPython runtime was found through the current interpreter, Python "
            f"launcher, or PATH. {PYTHON_GUIDANCE}"
        )
    return None, None, False, reason


def _try_compile(
    vcvarsall: Path | None,
) -> tuple[bool, str | None, str | None, str]:
    with tempfile.TemporaryDirectory(prefix="win_arm64_probe_") as temporary:
        work = Path(temporary)
        source = work / "probe.c"
        object_file = work / "probe.obj"
        executable = work / "probe.exe"
        log_file = work / "compiler.log"
        setup_log = work / "environment.log"
        batch_file = work / "probe.cmd"
        source.write_text("int main(void) { return 0; }\n", encoding="ascii")

        commands = ["@echo off"]
        if vcvarsall is not None:
            commands.extend(
                (
                    f'call "{vcvarsall}" x64_arm64 >"{setup_log}" 2>&1',
                    "if errorlevel 1 exit /b 11",
                )
            )
        commands.extend(
            (
                f'cl.exe /nologo /W0 /Fo:"{object_file}" /Fe:"{executable}" "{source}" /link /MACHINE:ARM64 >"{log_file}" 2>&1',
                "exit /b %errorlevel%",
            )
        )
        batch_file.write_text("\r\n".join(commands) + "\r\n", encoding="ascii")

        try:
            completed = subprocess.run(
                [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(batch_file)],
                cwd=work,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, None, None, f"Could not run the MSVC ARM64 probe: {error}"

        log = ""
        if log_file.is_file():
            log = log_file.read_text(encoding="utf-8", errors="replace").strip()
        if completed.returncode != 0:
            if completed.returncode == 11 and vcvarsall is not None:
                details = ""
                if setup_log.is_file():
                    details = setup_log.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                reason = (
                    "MSVC could not initialize its x64-to-ARM64 environment. "
                    + INSTALL_GUIDANCE
                )
                if details:
                    reason += f" Setup output: {details[:300]}"
                return False, None, None, reason
            detail = log or completed.stderr.strip() or completed.stdout.strip()
            return (
                False,
                None,
                None,
                detail or f"Compiler probe exited with code {completed.returncode}.",
            )
        if not object_file.is_file():
            return False, None, None, "MSVC reported success but did not produce the probe object."
        if not executable.is_file():
            return False, None, None, "MSVC reported success but did not produce the probe executable."

        object_bytes = object_file.read_bytes()
        try:
            machine = _coff_machine(object_bytes)
        except ValueError as error:
            return False, None, None, f"MSVC produced an invalid object: {error}"
        if machine != ARM64_COFF_MACHINE:
            return (
                False,
                _machine_name(machine),
                None,
                f"Compiler emitted COFF machine 0x{machine:04X}, not ARM64 (0xAA64).",
            )
        executable_machine = _pe_machine(executable)
        if executable_machine != ARM64_COFF_MACHINE:
            detected = (
                "an invalid PE image"
                if executable_machine is None
                else f"machine 0x{executable_machine:04X}"
            )
            return (
                False,
                _machine_name(machine),
                _machine_name(executable_machine)
                if executable_machine is not None
                else None,
                f"MSVC produced an executable with {detected}, not ARM64 (0xAA64).",
            )
        return (
            True,
            _machine_name(machine),
            _machine_name(executable_machine),
            "MSVC built and verified an ARM64 COFF object and PE executable.",
        )


def inspect_target() -> TargetStatus:
    """Report support only after finding ARM64 CPython and building an ARM64 executable."""
    if os.name != "nt":
        return TargetStatus(
            TARGET,
            False,
            None,
            None,
            False,
            None,
            None,
            None,
            False,
            "The MSVC ARM64 preflight can only run on Windows.",
        )

    candidates = _compiler_candidates()
    (
        python_executable,
        python_machine,
        python_runtime_found,
        python_reason,
    ) = _find_cpython_arm64()
    missing: list[str] = []
    if not candidates:
        missing.append("No MSVC ARM64 compiler was found. " + INSTALL_GUIDANCE)
    if not python_runtime_found:
        missing.append(python_reason)
    if missing:
        return TargetStatus(
            TARGET,
            False,
            str(candidates[0]) if candidates and candidates[0] is not None else None,
            None,
            False,
            None,
            str(python_executable) if python_executable else None,
            python_machine,
            False,
            "Windows ARM64 executable smoke build was not attempted because prerequisites are missing: "
            + " ".join(missing),
        )

    failures: list[str] = []
    detected_object_machine: str | None = None
    detected_executable_machine: str | None = None
    for vcvarsall in candidates:
        supported, object_machine, executable_machine, reason = _try_compile(vcvarsall)
        if supported:
            compiler = str(vcvarsall) if vcvarsall is not None else "cl.exe from PATH"
            return TargetStatus(
                TARGET,
                True,
                compiler,
                object_machine,
                True,
                executable_machine,
                str(python_executable),
                python_machine,
                True,
                reason,
            )
        failures.append(reason)
        detected_object_machine = detected_object_machine or object_machine
        detected_executable_machine = (
            detected_executable_machine or executable_machine
        )

    reason = next(
        (failure for failure in failures if failure),
        "The available MSVC installation could not build an ARM64 executable.",
    )
    if not any(
        phrase in reason
        for phrase in (ARM64_COMPONENT, "Visual Studio Installer")
    ):
        reason += f" {INSTALL_GUIDANCE}"
    if len(reason) > 500:
        reason = reason[:497] + "..."
    return TargetStatus(
        TARGET,
        False,
        str(candidates[0]) if candidates[0] is not None else "cl.exe from PATH",
        detected_object_machine,
        False,
        detected_executable_machine,
        str(python_executable),
        python_machine,
        True,
        "Windows ARM64 executable smoke build failed: " + reason,
    )


def main() -> int:
    status = inspect_target()
    print(json.dumps(asdict(status), indent=2))
    return 0 if status.supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
