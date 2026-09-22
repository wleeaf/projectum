# Installing Projectum with pip

Use the virtual-environment commands in the [README](../README.md#install).
They keep Projectum separate from system-managed Python packages and call the
same interpreter for installation and launch. No activation or Windows
PowerShell execution-policy change is needed.

Projectum requires Python 3.10 or newer, but that alone does **not** determine
whether installation will succeed. PySide6 contains native Qt libraries. A
compatible wheel must exist for the combination of Python version, operating
system version, and CPU architecture. Projectum's `py3-none-any` wheel does not
remove those dependency requirements.

For the Windows investigation and measured results, see the
[Windows installation audit](windows-install-audit.md).

## Compatibility

Checked against PyPI on September 22, 2026:

| Environment | pip installation constraints |
|---|---|
| Windows x64 | Use 64-bit CPython on a Qt-supported Windows version. There are no PySide6 6.5+ wheels for 32-bit Windows Python, even on a 64-bit OS. |
| Windows ARM64 | Recent PySide6 releases provide native ARM64 wheels. The Python interpreter architecture must match. |
| macOS Intel / Apple Silicon | PySide6 supplies universal2 wheels. Python 3.14 requires a newer PySide6 release whose wheels require macOS 13 or newer. |
| Linux x86-64 | Python 3.14 requires PySide6 wheels with glibc 2.34 or newer. Some older Python/PySide6 combinations can use glibc 2.28 wheels. |
| Linux ARM64 | Python 3.14 requires PySide6 wheels with glibc 2.39 or newer. Older combinations have different limits. |
| Alpine Linux / musl, 32-bit Linux | No matching official PySide6 6.5+ PyPI wheels. A normal pip install cannot supply Qt for these platforms. |

These are wheel availability constraints, not a claim that every OS combination
has been runtime-tested. Consult [PySide6 release files](https://pypi.org/project/PySide6/#files)
and [Qt's supported platforms](https://doc.qt.io/qt-6/supported-platforms.html)
for the selected version. Older Python versions may allow pip to select an older
compatible PySide6; forcing the latest Qt version can make compatibility worse.

## Match the error to its cause

### `The token '&&' is not a valid statement separator`

The old one-line install command used `&&`, which Windows PowerShell 5.1 does
not support. The README now uses separate commands that also work in that
shell. [Pipeline chain operators were introduced in PowerShell 7](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pipeline_chain_operators).

### `ensurepip is not available` while creating the environment

On Debian/Ubuntu, install `python3-venv` using the OS package manager, then
recreate the virtual environment. If using a separately installed Python
version, install its matching venv package. This failure happens before
Projectum is installed.

### `externally-managed-environment`

The system Python is owned by the OS or package manager. This is common on
Linux distributions and Homebrew Python. Install into a virtual environment;
`pip install --user` does not necessarily bypass this restriction. Do not use
`sudo pip` or `--break-system-packages` to install Projectum.

See the [externally managed environments specification](https://packaging.python.org/en/latest/specifications/externally-managed-environments/)
and [Qt's virtual-environment instructions](https://doc.qt.io/qtforpython-6/gettingstarted.html).

### `No matching distribution found for PySide6` or `Requires-Python`

1. Upgrade pip **inside the virtual environment**.
2. Check the interpreter version and architecture with the command below.
3. Check the compatibility table. For example, Python 3.14 on macOS 12 cannot
   use the newer Qt wheels, while the older Qt wheels reject Python 3.14.
   A Python 3.12 environment can resolve an older compatible Qt release.
4. For an unsupported platform, use a supported OS/interpreter combination.
   Projectum cannot fix missing upstream native wheels with a dependency pin.

Python older than 3.10 cannot install the current Projectum release. Use standard
64-bit CPython; other interpreter builds need their own compatible Qt wheels.

### Installation succeeds, but launch reports `libEGL.so.1`, `libGL.so.1`, or an `xcb` plugin error

This is a Qt runtime dependency or display problem, rather than a pip build
failure. Qt wheels include Qt itself, but still need OS graphics libraries and
a desktop display. For Debian/Ubuntu, common missing runtime packages are:

```bash
sudo apt-get install libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0
```

An `xcb` message alone does not identify which library is missing. Use
`QT_DEBUG_PLUGINS=1 .venv/bin/projectum` on Linux for the detailed loader error,
and consult [Qt's Linux requirements](https://doc.qt.io/qt-6/linux-requirements.html).
A headless server or container also needs a display for interactive use;
`QT_QPA_PLATFORM=offscreen` is for automated tests.

### `projectum` is not recognized / command not found

Use `.venv/bin/projectum` on Linux/macOS or `.venv\Scripts\projectum` on Windows.
Version 2.4.1 also supports `python -m projectum` using that environment's
Python, so launching does not depend on the Scripts directory being on PATH.
A successful installation into one interpreter does not install the command
into every Python environment or add its scripts directory to your shell PATH.

### Download timeout / no space left on device

The published 2.4.0 package downloads Qt Essentials and Addons. Version 2.4.1
depends on Essentials alone, removing unused Qt modules. Essentials
is still a large native package. Check the error for a timeout, proxy failure,
or insufficient storage before treating it as an unsupported platform. For a
slow connection, retry the same environment's pip command with `--timeout 120`.

## Information to include in an installation bug report

Run these using the **same Python executable used for installation** (for
example, `.venv/bin/python` or `.venv\Scripts\python`):

```text
python -VV
python -m pip --version
python -c "import platform, struct; print(platform.platform()); print(platform.machine()); print(struct.calcsize('P') * 8, 'bit'); print(platform.libc_ver())"
python -m pip debug --verbose
```

Include the original install command and its complete error output. Distinguish
an error during `pip install` from one when launching `projectum`. Remove any
private package-index credentials from logs before sharing them.

## Release validation

CI builds the sdist and builds the wheel from that sdist. It installs both in a
clean virtual environment, runs `pip check`, verifies the packaged icon and
entry point, and boots the installed app outside the source checkout. The PyPI
publication workflow waits for these checks. Local equivalent:

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
python packaging/check_install.py dist
```

The smoke check uses a temporary config directory and closes the app before its
scheduled update check. Running it does not publish anything.
