# Linux ARM64 target

This target adapter is isolated from the shared Windows packager. Run
`python target.py` to print host and target capabilities without running a
build.

To prepare a compile-only recipe, run from a Linux build host:

```sh
python target.py --launcher-source path/to/launcher.c --output dist/my-launcher
```

On Windows, invoke the adapter inside Linux WSL; it intentionally rejects a
Windows host. A Linux x86_64 VM with an AArch64 cross compiler and target files
is sufficient to compile; QEMU and the target libraries are needed to run the
result under emulation.

For a CPython link smoke test, use `smoke_launcher.c` as the launcher source.
This only verifies that a Linux ARM64 executable can be compiled and linked;
it is not an application bundle or a production launcher.

The adapter prints argv-safe commands (create output directory, then invoke the
compiler) but never executes them. It emits the compiler command only after
checking the source file, Linux build host, `aarch64-linux-*` compiler target,
existing sysroot, CPython headers/shared library, and AArch64 ELF interpreter.
Set `LINUX_ARM64_CC`, `LINUX_ARM64_SYSROOT`, and `LINUX_ARM64_PYTHON_ROOT` to
select the toolchain and target runtime; `readelf` must be on `PATH`.
The preflight reports unset settings, missing runtime files, and architecture
verification failures separately so a missing `readelf` is not mistaken for a
missing CPython runtime.

The recipe only compiles a supplied C launcher against CPython. It does not
bundle an application or make a self-contained executable: Linux application
packaging/launcher support is not implemented, so `application_build_available`
remains false even when a compile recipe can be prepared. A Windows CPython
runtime and the existing Windows launcher cannot produce a Linux AArch64
executable. The current smoke artifact at `build/linux-arm64-smoke`
is an ELF64 AArch64 executable and dynamically links `libpython3.14.so.1.0`;
it was run under QEMU with a matching CPython runtime. It is not a packaged
application and needs that runtime to execute.
The verified runtime came from the official
`astral-sh/python-build-standalone` release `20261026`, asset
`cpython-3.14.10+20261026-aarch64-unknown-linux-gnu-install_only.tar.gz`
(SHA-256 `dcc31ce7b9ddbbffac160da7fff0cc069054f43eca52382a7d0eebf94e985b88`).

Run the isolated tests with
`python -m unittest discover -s targets/linux_arm64 -p test_*.py`.
