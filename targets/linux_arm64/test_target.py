import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from target import TargetConfig, build_recipe, describe_target, discover_config


class TargetTests(unittest.TestCase):
    def test_discovers_compiler_from_configuration_and_checks_its_triplet(self):
        run = Mock(
            return_value=subprocess.CompletedProcess(
                args=["compiler", "-dumpmachine"],
                returncode=0,
                stdout="aarch64-linux-gnu\n",
                stderr="",
            )
        )
        config = discover_config(
            {"LINUX_ARM64_CC": "custom-gcc"},
            which=lambda name: f"/host/bin/{name}" if name == "custom-gcc" else None,
            run=run,
        )

        self.assertEqual(config.compiler, "/host/bin/custom-gcc")
        self.assertEqual(config.compiler_triplet, "aarch64-linux-gnu")
        run.assert_called_once_with(["/host/bin/custom-gcc", "-dumpmachine"])

    def test_rejects_compiler_for_the_wrong_target_architecture(self):
        config = TargetConfig(
            compiler="/host/bin/gcc",
            compiler_triplet="x86_64-linux-gnu",
            compiler_probe_error=None,
            sysroot=None,
            python_root=None,
            readelf=None,
            python_machine=None,
            runtime_probe_error=None,
        )

        report = describe_target(config)

        self.assertFalse(report["build_available"])
        self.assertFalse(report["requirements"][0]["available"])
        self.assertIsNone(report["build_command"])

    def test_checks_sysroot_and_cpython_runtime_layout_and_elf_machine(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sysroot = root / "sysroot"
            runtime = root / "python"
            (runtime / "bin").mkdir(parents=True)
            (runtime / "include" / "python3.14").mkdir(parents=True)
            (runtime / "lib").mkdir()
            (sysroot / "usr" / "include").mkdir(parents=True)
            (runtime / "bin" / "python3").write_bytes(b"ELF")
            (runtime / "include" / "python3.14" / "Python.h").write_text("", encoding="ascii")
            (runtime / "lib" / "libpython3.14.so").write_bytes(b"ELF")

            run = Mock(
                side_effect=[
                    subprocess.CompletedProcess([], 0, "aarch64-linux-gnu\n", ""),
                    subprocess.CompletedProcess([], 0, "  Machine: AArch64\n", ""),
                ]
            )
            config = discover_config(
                {
                    "LINUX_ARM64_CC": "aarch64-linux-gnu-gcc",
                    "LINUX_ARM64_SYSROOT": str(sysroot),
                    "LINUX_ARM64_PYTHON_ROOT": str(runtime),
                },
                which=lambda name: {
                    "aarch64-linux-gnu-gcc": "/host/bin/aarch64-linux-gnu-gcc",
                    "readelf": "/host/bin/readelf",
                }.get(name),
                run=run,
            )

            report = describe_target(config, host_system="windows", host_machine="amd64")

            self.assertEqual(report["host"], {"system": "windows", "machine": "amd64"})
            self.assertTrue(report["requirements"][0]["available"])
            self.assertTrue(report["requirements"][1]["available"])
            self.assertTrue(report["requirements"][2]["available"])
            self.assertTrue(report["requirements"][3]["available"])
            self.assertFalse(report["build_available"])
            self.assertIsNone(report["build_command"])
            self.assertIn("Not implemented", report["blockers"][-1])

    def test_does_not_run_or_advertise_a_build_command_without_tools(self):
        config = discover_config(
            {},
            which=lambda _name: None,
            run=Mock(),
        )

        report = describe_target(config, host_system="windows", host_machine="amd64")

        self.assertFalse(report["build_available"])
        self.assertIsNone(report["build_command"])
        self.assertEqual(report["host"], {"system": "windows", "machine": "amd64"})
        self.assertFalse(report["requirements"][3]["available"])

    def test_preflight_names_each_missing_runtime_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "python"
            runtime.mkdir()
            config = TargetConfig(
                compiler=None,
                compiler_triplet=None,
                compiler_probe_error=None,
                sysroot=None,
                python_root=runtime,
                readelf=None,
                python_machine=None,
                runtime_probe_error=None,
            )

            report = describe_target(config)

            runtime_requirement = report["requirements"][2]
            self.assertFalse(runtime_requirement["available"])
            for missing in (
                "CPython interpreter is missing",
                "CPython headers",
                "CPython shared library",
                "readelf is unavailable",
            ):
                self.assertIn(missing, runtime_requirement["detail"])
            self.assertIn("LINUX_ARM64_SYSROOT is not set.", report["blockers"])

    def test_preflight_rejects_non_aarch64_cpython_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "python"
            (runtime / "bin").mkdir(parents=True)
            (runtime / "include" / "python3.14").mkdir(parents=True)
            (runtime / "lib").mkdir()
            (runtime / "bin" / "python3").write_bytes(b"ELF")
            (runtime / "include" / "python3.14" / "Python.h").write_text("", encoding="ascii")
            (runtime / "lib" / "libpython3.14.so").write_bytes(b"ELF")
            config = TargetConfig(
                compiler="/host/bin/aarch64-linux-gnu-gcc",
                compiler_triplet="aarch64-linux-gnu",
                compiler_probe_error=None,
                sysroot=root,
                python_root=runtime,
                readelf="/host/bin/readelf",
                python_machine="Advanced Micro Devices X86-64",
                runtime_probe_error=None,
            )

            runtime_requirement = describe_target(config)["requirements"][2]

            self.assertFalse(runtime_requirement["available"])
            self.assertIn("must be an AArch64 ELF binary", runtime_requirement["detail"])
            self.assertIn("Advanced Micro Devices X86-64", runtime_requirement["detail"])

    def test_build_recipe_quotes_paths_and_never_claims_application_packaging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sysroot = root / "linux sysroot"
            runtime = root / "target python"
            launcher = root / "linux launcher.c"
            output = root / "dist folder" / "sample app"
            (sysroot / "usr" / "include").mkdir(parents=True)
            (runtime / "bin").mkdir(parents=True)
            (runtime / "include" / "python3.14").mkdir(parents=True)
            (runtime / "lib").mkdir()
            (runtime / "bin" / "python3").write_bytes(b"ELF")
            (runtime / "include" / "python3.14" / "Python.h").write_text("", encoding="ascii")
            (runtime / "lib" / "libpython3.14.so").write_bytes(b"ELF")
            launcher.write_text("int main(void) { return 0; }\n", encoding="ascii")
            config = TargetConfig(
                compiler="/toolchain/bin/aarch64-linux-gnu-gcc",
                compiler_triplet="aarch64-linux-gnu",
                compiler_probe_error=None,
                sysroot=sysroot,
                python_root=runtime,
                readelf="/toolchain/bin/readelf",
                python_machine="AArch64",
                runtime_probe_error=None,
            )

            recipe = build_recipe(config, launcher, output, host_system="linux")

            self.assertTrue(recipe["recipe_available"])
            self.assertFalse(recipe["application_build_available"])
            self.assertEqual(recipe["commands"][0], ["mkdir", "-p", str(output.parent)])
            argv = recipe["compile_argv"]
            self.assertEqual(argv[0], config.compiler)
            self.assertIn(f"--sysroot={sysroot}", argv)
            self.assertIn(f"-I{runtime / 'include' / 'python3.14'}", argv)
            self.assertIn(f"-L{runtime / 'lib'}", argv)
            self.assertIn("-l:libpython3.14.so", argv)
            self.assertEqual(argv[-2:], ["-o", str(output)])
            self.assertIn(shlex.quote(str(launcher)), recipe["shell_command"])
            self.assertIn(shlex.quote(f"--sysroot={sysroot}"), recipe["shell_command"])

    def test_build_recipe_fails_closed_for_windows_host_or_missing_launcher_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = TargetConfig(
                compiler="/toolchain/bin/aarch64-linux-gnu-gcc",
                compiler_triplet="aarch64-linux-gnu",
                compiler_probe_error=None,
                sysroot=root,
                python_root=None,
                readelf="/toolchain/bin/readelf",
                python_machine=None,
                runtime_probe_error=None,
            )
            source = root / "missing.c"
            output = root / "launcher"

            recipe = build_recipe(config, source, output, host_system="windows")

            self.assertFalse(recipe["recipe_available"])
            self.assertIsNone(recipe["compile_argv"])
            self.assertIsNone(recipe["shell_command"])
            self.assertTrue(any("Linux build host" in blocker for blocker in recipe["blockers"]))
            self.assertTrue(any("C source does not exist" in blocker for blocker in recipe["blockers"]))


if __name__ == "__main__":
    unittest.main()
