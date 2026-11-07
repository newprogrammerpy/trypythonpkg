# Windows ARM64 preflight

Run `python targets\win_arm64\build_config.py` from the packager root. The
preflight reports whether both an MSVC ARM64 compiler and a CPython ARM64
runtime are available. It builds a minimal executable in a temporary directory
only when both prerequisites are found, and reports success only after
verifying the COFF object and executable PE machine as ARM64 (`0xAA64`). The
temporary executable is removed automatically; this does not enable or build
the packager's executable or MSI output.

If the ARM64 compiler target is missing, open Visual Studio Installer, choose
**Modify** for Build Tools, then under **Individual components** enable:

- **C++ ARM64 build tools**,
  component ID `Microsoft.VisualStudio.Component.VC.Tools.ARM64`.
- **Windows 11 SDK (10.0.26100.0)**,
  component ID `Microsoft.VisualStudio.Component.Windows11SDK.26100`.

The component IDs are used by the preflight's guidance and can be found in
Visual Studio Installer under **Individual components**. The preflight does not
install tools, modify Visual Studio, or accept licenses.

Install the **Windows installer (ARM64)** build of CPython from
[python.org's Windows downloads](https://www.python.org/downloads/windows/).
The preflight discovers registered interpreters with `py -0p` and checks the
PE machine of `python.exe` and an adjacent CPython DLL; an AMD64 interpreter
does not satisfy the ARM64 runtime requirement.

Run its tests with
`python -m unittest discover -s targets\win_arm64 -v`.
