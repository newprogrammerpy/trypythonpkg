# Windows x86 target

Check whether this machine has MSVC's x86 toolchain and a complete 32-bit
CPython runtime:

```powershell
python -m targets.winx32.target probe
```

The probe compiles and verifies a temporary x86 C executable and Windows
resource, then checks the target runtime. This is a capability check, not an
application build or run. A ready result only means the build prerequisites
are present.

Build a standalone x86 executable when a full x86 CPython installation is
available:

```powershell
python -m targets.winx32.target build app.py app.exe --python-root "C:\Path\To\Python-x86"
```

Provide the target runtime explicitly with `--python-root` or
`WINX32_PYTHON_ROOT`; otherwise the adapter checks registered Python
installations. Point it at the root of a full **32-bit CPython installation**,
containing `python.exe`, `python3.dll`, its versioned `python*.dll`, runtime
`vcruntime140.dll`, `Lib`, and `DLLs`. The adapter rejects non-x86 interpreter,
DLL, and extension-module binaries. The x86 runtime is staged separately from
the x64 packager because `vcruntime140_1.dll` is x64-only and isn't required by
the official x86 CPython runtime.

The compiler is initialized with `vcvarsall.bat x86`, then the adapter locates
`cl.exe` and the Windows SDK `rc.exe` in that environment. If Visual Studio
cannot be found automatically, set `WINX32_VCVARSALL` to the full path of its
`VC\Auxiliary\Build\vcvarsall.bat`. This must be an existing file; an invalid
explicit path fails closed rather than silently selecting another install.
The installation must include the MSVC C++ x86 build tools and Windows SDK.
For example, configure both locations in PowerShell, then run the preflight
before building:

```powershell
$env:WINX32_PYTHON_ROOT = "C:\Path\To\Python-x86"
$env:WINX32_VCVARSALL = "C:\Path\To\VisualStudio\VC\Auxiliary\Build\vcvarsall.bat"
python -m targets.winx32.target probe
python -m targets.winx32.target build app.py app.exe
```

Repeat `--include PATH` to embed additional files. The build fails closed if a
prerequisite is missing or if the resulting executable is not x86. This target
is isolated from the existing x64 packager and does not build an MSI. A
successful build creates the application executable; running that executable
is a separate smoke check and requires Windows x86 execution support.

Run its tests from the repository root with:

```powershell
python -m unittest targets.winx32.test_target test_pypack -v
```
