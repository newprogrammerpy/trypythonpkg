"""Check host prerequisites for building an iOS device app with Python."""

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Literal, Sequence


Status = Literal["pass", "blocked", "action"]


@dataclass(frozen=True)
class RequirementCheck:
    name: str
    status: Status
    detail: str


def check_requirements(
    target: str = "device",
    *,
    host_platform: str | None = None,
    find_executable: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[RequirementCheck]:
    """Report prerequisites without installing tools or changing machine state."""
    if target not in {"device", "simulator"}:
        raise ValueError(f"Unsupported iOS target: {target}")

    host_platform = sys.platform if host_platform is None else host_platform
    sdk = "iphoneos" if target == "device" else "iphonesimulator"
    checks: list[RequirementCheck] = []
    is_macos = host_platform == "darwin"
    checks.append(
        RequirementCheck(
            "Build host",
            "pass" if is_macos else "blocked",
            "macOS is available."
            if is_macos
            else "iOS apps must be built with Apple's Xcode toolchain on macOS; "
            "Windows cannot produce a valid signed iOS app.",
        )
    )

    xcodebuild = find_executable("xcodebuild") if is_macos else None
    has_xcode = bool(xcodebuild)
    checks.append(
        RequirementCheck(
            "Xcode",
            "pass" if has_xcode else "blocked",
            "xcodebuild is available."
            if has_xcode
            else (
                "Install and select a full Xcode installation on macOS; "
                "Windows cannot run Xcode."
                if not is_macos
                else "xcodebuild was not found. A standalone Command Line Tools "
                "installation is not a substitute for the iOS SDK in Xcode."
            ),
        )
    )

    xcrun = find_executable("xcrun") if is_macos and has_xcode else None
    if not is_macos or not has_xcode or not xcrun:
        checks.append(
            RequirementCheck(
                f"{sdk} SDK",
                "blocked",
                "Not checked because a macOS Xcode toolchain is unavailable."
                if not is_macos or not has_xcode
                else "xcrun was not found in the selected Xcode toolchain.",
            )
        )
    else:
        try:
            result = run(
                [xcrun, "--sdk", sdk, "--show-sdk-path"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            checks.append(
                RequirementCheck(f"{sdk} SDK", "blocked", f"Could not run xcrun: {error}")
            )
        else:
            sdk_path = result.stdout.strip()
            sdk_available = result.returncode == 0 and bool(sdk_path)
            detail = (
                f"Found SDK at {sdk_path}."
                if sdk_available
                else (result.stderr.strip() or f"xcrun could not locate the {sdk} SDK.")
            )
            checks.append(
                RequirementCheck(
                    f"{sdk} SDK",
                    "pass" if sdk_available else "blocked",
                    detail,
                )
            )

    if target == "device":
        checks.append(
            RequirementCheck(
                "Device signing",
                "action",
                "Configure an app bundle ID, Apple team, signing identity, and "
                "matching provisioning profile (or Xcode-managed signing) for "
                "this app. Machine checks cannot validate app-specific signing.",
            )
        )
    checks.append(
        RequirementCheck(
            "Python runtime",
            "action",
            "Integrate an iOS-built Python runtime, for example through BeeWare "
            "Briefcase or Python-Apple-support; a Windows CPython runtime is not usable.",
        )
    )
    return checks


def format_report(target: str, checks: Sequence[RequirementCheck]) -> str:
    lines = [f"iOS {target} requirements:"]
    labels = {"pass": "PASS", "blocked": "BLOCKED", "action": "ACTION"}
    lines.extend(
        f"[{labels[check.status]}] {check.name}: {check.detail}" for check in checks
    )
    if all(check.status == "pass" for check in checks):
        lines.append("Ready: all checked prerequisites are present.")
    else:
        lines.append("Not ready: resolve blocked checks and required actions above.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check prerequisites for an iOS device or simulator build."
    )
    parser.add_argument(
        "--target",
        choices=("device", "simulator"),
        default="device",
        help="check the physical-device or simulator SDK (default: device)",
    )
    args = parser.parse_args(argv)
    checks = check_requirements(args.target)
    print(format_report(args.target, checks))
    return 0 if all(check.status == "pass" for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
