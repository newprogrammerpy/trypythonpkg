"""Fail-closed preflight for an iOS Xcode project embedding Python."""

import argparse
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence


Status = Literal["pass", "blocked", "action"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class BuildInputs:
    python_xcframework: Path
    app_source: Path
    bundle_id: str
    team_id: str
    provisioning_profile: Path | None = None
    signing_identity: str | None = None
    xcode_managed_signing: bool = False


def _check_xcframework(path: Path) -> Check:
    info_path = path / "Info.plist"
    if not path.is_dir() or not info_path.is_file():
        return Check(
            "Python XCFramework",
            "blocked",
            f"Expected an existing Python.xcframework with Info.plist: {path}",
        )
    try:
        with info_path.open("rb") as info_file:
            metadata = plistlib.load(info_file)
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        return Check("Python XCFramework", "blocked", f"Cannot read Info.plist: {error}")

    if not isinstance(metadata, dict):
        return Check(
            "Python XCFramework", "blocked", "Info.plist must contain a dictionary."
        )
    libraries = metadata.get("AvailableLibraries", [])
    if not isinstance(libraries, list):
        return Check(
            "Python XCFramework",
            "blocked",
            "Info.plist has no valid AvailableLibraries list.",
        )
    device_slice = any(
        library.get("SupportedPlatform") == "ios"
        and "arm64" in library.get("SupportedArchitectures", [])
        and library.get("LibraryPath") == "Python.framework"
        and isinstance(library.get("LibraryIdentifier"), str)
        and (
            path
            / library["LibraryIdentifier"]
            / library["LibraryPath"]
        ).is_dir()
        and (
            path
            / library["LibraryIdentifier"]
            / library["LibraryPath"]
            / "Python"
        ).is_file()
        for library in libraries
        if isinstance(library, dict)
    )
    if not device_slice:
        return Check(
            "Python XCFramework",
            "blocked",
            "No iOS device arm64 slice was found; simulator-only XCFrameworks "
            "cannot be used for a physical device build.",
        )
    return Check(
        "Python XCFramework",
        "pass",
        "Found an iOS device arm64 slice. Confirm this runtime's version and "
        "App Store compliance for the intended release.",
    )


def _check_app_source(path: Path) -> Check:
    entry_point = path / "main.py"
    if not path.is_dir() or not entry_point.is_file():
        return Check(
            "Python app source",
            "blocked",
            f"Expected a source directory containing main.py: {path}",
        )
    return Check("Python app source", "pass", f"Found {entry_point}.")


def _check_signing(inputs: BuildInputs) -> list[Check]:
    bundle_id_is_valid = bool(
        re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?",
            inputs.bundle_id,
        )
    )
    team_id_is_valid = bool(re.fullmatch(r"[A-Z0-9]{10}", inputs.team_id))
    checks = [
        Check(
            "Bundle identifier",
            "pass" if bundle_id_is_valid else "blocked",
            "A reverse-DNS bundle identifier is set."
            if bundle_id_is_valid
            else "Provide a reverse-DNS bundle identifier such as com.example.app.",
        ),
        Check(
            "Apple team",
            "pass" if team_id_is_valid else "blocked",
            "A 10-character Apple team ID is set."
            if team_id_is_valid
            else "Provide the 10-character Apple Developer team ID.",
        ),
    ]
    if inputs.xcode_managed_signing:
        checks.append(
            Check(
                "Signing configuration",
                "action",
                "Xcode-managed signing was requested. The Apple account, team "
                "access, and signing capability must still be configured in Xcode.",
            )
        )
    else:
        profile_exists = bool(
            inputs.provisioning_profile and inputs.provisioning_profile.is_file()
        )
        checks.extend(
            (
                Check(
                    "Provisioning profile",
                    "action" if profile_exists else "blocked",
                    f"Found {inputs.provisioning_profile}; its app ID, team, "
                    "expiration, and entitlements are not inspected."
                    if profile_exists
                    else "Provide an existing app-specific provisioning profile.",
                ),
                Check(
                    "Signing identity",
                    "action" if bool(inputs.signing_identity and inputs.signing_identity.strip())
                    else "blocked",
                    "A signing identity was provided; Xcode must verify it is "
                    "available in the keychain and matches the profile."
                    if inputs.signing_identity and inputs.signing_identity.strip()
                    else "Provide the code-signing identity name; its keychain "
                    "availability cannot be verified by this preflight.",
                ),
            )
        )
    return checks


def check_build_capability(
    inputs: BuildInputs,
    *,
    host_platform: str | None = None,
    find_executable: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[Check]:
    """Check local build inputs without invoking a build or modifying the host."""
    host_platform = sys.platform if host_platform is None else host_platform
    if host_platform != "darwin":
        return [
            Check(
                "Build host",
                "blocked",
                "A signed iOS device app must be built with Apple's Xcode toolchain "
                "on macOS; Windows and other hosts are unsupported.",
            ),
            Check(
                "Apple tools and SDK",
                "blocked",
                "Not checked because this host is not macOS.",
            ),
            _check_xcframework(inputs.python_xcframework),
            _check_app_source(inputs.app_source),
            *_check_signing(inputs),
        ]

    xcodebuild = find_executable("xcodebuild")
    xcrun = find_executable("xcrun")
    xcode_select = find_executable("xcode-select")
    if not xcodebuild or not xcrun or not xcode_select:
        tools_check = Check(
            "Apple tools and SDK",
            "blocked",
            "Full Xcode is required (xcodebuild, xcrun, and xcode-select); "
            "Command Line Tools alone are insufficient.",
        )
    else:
        try:
            selected = run(
                [xcode_select, "-p"],
                check=False,
                capture_output=True,
                text=True,
            )
            developer_dir = Path(selected.stdout.strip())
            full_xcode_selected = (
                selected.returncode == 0
                and developer_dir.name == "Developer"
                and developer_dir.parent.name == "Contents"
                and developer_dir.parent.parent.suffix == ".app"
            )
            sdk_result = run(
                [xcrun, "--sdk", "iphoneos", "--show-sdk-path"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            tools_check = Check(
                "Apple tools and SDK", "blocked", f"Could not inspect Xcode: {error}"
            )
        else:
            sdk_path = Path(sdk_result.stdout.strip())
            sdk_available = (
                sdk_result.returncode == 0 and sdk_path.is_dir()
            )
            if not full_xcode_selected:
                tools_check = Check(
                    "Apple tools and SDK",
                    "blocked",
                    "The selected developer directory is not inside a full "
                    "Xcode.app installation.",
                )
            elif not sdk_available:
                tools_check = Check(
                    "Apple tools and SDK",
                    "blocked",
                    sdk_result.stderr.strip()
                    or "The selected Xcode installation has no available iPhoneOS SDK.",
                )
            else:
                tools_check = Check(
                    "Apple tools and SDK",
                    "pass",
                    f"Full Xcode selected; iPhoneOS SDK found at {sdk_path}.",
                )

    return [
        Check("Build host", "pass", "macOS is available."),
        tools_check,
        _check_xcframework(inputs.python_xcframework),
        _check_app_source(inputs.app_source),
        *_check_signing(inputs),
    ]


def format_report(checks: Sequence[Check]) -> str:
    labels = {"pass": "PASS", "blocked": "BLOCKED", "action": "ACTION"}
    lines = ["iOS ARM64 build preflight (no build or signing is performed):"]
    lines.extend(
        f"[{labels[check.status]}] {check.name}: {check.detail}" for check in checks
    )
    if any(check.status == "blocked" for check in checks):
        lines.append("Not ready: at least one required capability or input is missing.")
    elif any(check.status == "action" for check in checks):
        lines.append(
            "Required inputs are present, but signing remains unverified. Xcode "
            "must validate signing/provisioning and perform the actual build; "
            "no .app or .ipa was produced."
        )
    else:
        lines.append(
            "Preflight passed. Xcode must still validate signing/provisioning and "
            "perform the actual build; no .app or .ipa was produced."
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only preflight for an Xcode app embedding Python on iOS."
    )
    parser.add_argument("--python-xcframework", type=Path, required=True)
    parser.add_argument(
        "--app-source",
        type=Path,
        required=True,
        help="directory containing the Python app's main.py",
    )
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--team-id", required=True)
    signing = parser.add_mutually_exclusive_group(required=True)
    signing.add_argument("--provisioning-profile", type=Path)
    signing.add_argument("--xcode-managed-signing", action="store_true")
    parser.add_argument("--signing-identity")
    args = parser.parse_args(argv)

    if args.provisioning_profile and not args.signing_identity:
        parser.error("--signing-identity is required with --provisioning-profile")
    inputs = BuildInputs(
        python_xcframework=args.python_xcframework,
        app_source=args.app_source,
        bundle_id=args.bundle_id,
        team_id=args.team_id,
        provisioning_profile=args.provisioning_profile,
        signing_identity=args.signing_identity,
        xcode_managed_signing=args.xcode_managed_signing,
    )
    checks = check_build_capability(inputs)
    print(format_report(checks))
    return 1 if any(check.status == "blocked" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
