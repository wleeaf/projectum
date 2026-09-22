# Windows pip installation audit

Investigated September 22, 2026 against the published Projectum 2.4.0 wheel,
PySide6 6.11.2, and pip 26.2.1. The original failure log is unavailable, so this
report distinguishes reproduced failures, measured risks, and causes that the
checks did not support.

## Findings

| Candidate | Evidence | Assessment |
|---|---|---|
| Unsupported interpreter | Windows-targeted pip resolution fails for Python 3.9, 32-bit CPython 3.12, free-threaded CPython 3.14, and PyPy 3.10. It succeeds for standard x64 CPython 3.10, 3.12, 3.13, 3.14 and ARM64 CPython 3.12. | Confirmed installation blockers for these interpreter combinations. A 64-bit OS alone is insufficient. |
| Scripts directory missing from PATH | Installing with Windows Python emits `projectum.exe ... is not on PATH`. The executable is present in `Scripts` after installation. | Confirmed way for installation to succeed but the documented launch command to fail. |
| Windows path length | The Essentials wheel contains a 151-character relative path. Appending it to a representative Microsoft Store Python user-install path produces a 292-character filename. | Strong candidate for `WinError 206` / long-path-related file-not-found errors where Windows long paths are disabled. The arithmetic is verified; this failure was not reproduced on native Windows. |
| Unnecessary native dependencies | Published metadata requires the full PySide6 metapackage. Its Windows wheels download about 247 MB and unpack about 676 MB. About 169 MB of download / 462 MB unpacked is Addons plus the wrapper; Projectum imports only Essentials modules. | Confirmed packaging overhead that increases timeout and disk-space exposure. Fixed by depending on `PySide6-Essentials`. This does not remove Essentials' own long paths or architecture limits. |
| PowerShell 5.1 syntax | The old README used `pip install projectum && projectum`. `&&` is only supported starting with PowerShell 7. | Confirmed command incompatibility. Instructions now use separate commands. |
| Windows version / native DLLs | Qt 6.11 supports Windows 10 1809+ and Windows 11. Its Windows wheel tag does not encode that minimum OS version. Windows pip installed all packages under Wine, but importing QtCore failed because that environment lacks `icuuc.dll`. | OS/runtime compatibility is a real constraint. The Wine result is an environment limitation, not proof that the wheel fails on supported Windows. |

The GUI imports are QtCore, QtGui, and QtWidgets. The SVG icon also uses the
SVG image plugin included in Essentials. No application module uses Addons'
WebEngine, 3D, multimedia, or chart bindings.

## Candidates checked but not established as the cause

- **Missing Visual C++ redistributable:** the inspected Essentials and shiboken
  wheels already bundle `msvcp140.dll`, `vcruntime140.dll`,
  `vcruntime140_1.dll`, and related runtime libraries. A broken installation can
  still cause DLL errors, but a missing separate runtime installer is not the
  leading explanation for a clean install of these versions.
- **Broken Windows console launcher:** the installed `projectum.exe` successfully
  invoked its configured Python entry point in a controlled test with the GUI
  replaced by a short-lived stub. That isolates launcher behavior from Wine's
  ICU limitation.
- **An app already running during reinstall:** a controlled reinstall while the
  Windows launcher was running succeeded but emitted a temporary-directory
  cleanup warning. Deleting a loaded DLL separately produced `WinError 5`.
  Loaded binaries can complicate upgrades, but this did not reproduce an
  initial-install failure. Close the app before manually upgrading.
- **Missing app files or invalid wheel metadata:** the published wheel and the
  rebuilt local wheel/sdist install and boot in clean Linux environments.
  Assets, entry points, and dependency checks pass.

## Changes and validation

- Replaced the full PySide6 requirement with `PySide6-Essentials` in package
  metadata, source requirements, and the AppImage recipe.
- Added `python -m projectum`, allowing launch with the exact interpreter used
  for installation, without needing its Scripts directory on PATH.
- Extended package smoke checks to cover the module launcher and load the
  native Windows platform plugin on Windows CI.
- Retained the earlier cross-platform wheel/sdist installation checks and
  publication gate. The Windows CI jobs must still run on GitHub; local Wine
  checks are not a substitute for them.

The reduced dependency set passes all 236 tests in a clean environment with
neither PySide6 Addons nor the PySide6 metapackage installed. Both rebuilt
package formats pass installation, `pip check`, entry-point launch, and module
launch on Linux. These changes are included in Projectum 2.4.1.

## Reproducing dependency resolution

These commands download metadata rather than running Windows code:

```text
python -m pip install --dry-run --ignore-installed --only-binary=:all: --platform win_amd64 --implementation cp --python-version 3.12 --abi cp312 projectum==2.4.0
python -m pip install --dry-run --ignore-installed --only-binary=:all: --platform win32 --implementation cp --python-version 3.12 --abi cp312 projectum==2.4.0
```

Use `--platform win_amd64 --python-version 3.14 --abi cp314t` for the
free-threaded case. These checks establish wheel availability, not native
runtime compatibility. Resolution can change as dependency releases appear.

## References

- [Qt package contents](https://doc.qt.io/qtforpython-6/package_details.html)
- [PySide6 wheel files](https://pypi.org/project/PySide6/#files)
- [Qt Windows support](https://doc.qt.io/qt-6/windows.html)
- [Python's Windows path-length guidance](https://docs.python.org/3/using/windows.html#removing-the-max-path-limitation)
- [PowerShell pipeline chain operators](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pipeline_chain_operators)
- [Windows ICU availability](https://learn.microsoft.com/en-us/windows/win32/intl/international-components-for-unicode--icu-)
