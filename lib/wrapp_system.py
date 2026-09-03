"""Portable, dependency-free helpers for a concise system overview.

The module deliberately reports only non-sensitive, local machine details so
that its output is suitable for a command-line status screen.
"""

import ctypes
import os
import platform
import shutil
from pathlib import Path
from typing import Dict, Optional

from lib.wrapp_terminal import Terminal


__version__ = "0.26.01"


def format_bytes(value: int) -> str:
    """Format a byte count using compact binary units."""

    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return "{0} {1}".format(int(amount), unit)
            return "{0:.1f} {1}".format(amount, unit)
        amount /= 1024
    return "{0} B".format(value)


def get_computer_name() -> str:
    """Return the local computer name, or a clear fallback."""

    return platform.node() or os.environ.get("COMPUTERNAME") or "unknown"


def get_platform_system() -> str:
    """Return the canonical operating-system family name."""

    return platform.system() or "unknown"


def get_computer_description() -> str:
    """Return a short architecture and CPU-thread description."""

    architecture = platform.machine() or "unknown architecture"
    cpu_count = os.cpu_count()
    if cpu_count is None:
        return architecture
    return "{0}, {1} CPU threads".format(architecture, cpu_count)


def _read_os_release() -> Dict[str, str]:
    """Read the small subset of /etc/os-release needed for Linux labels."""

    os_release = Path("/etc/os-release")
    values = {}
    try:
        lines = os_release.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return values

    for line in lines:
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def get_operating_system() -> str:
    """Return a human-readable OS label, including Linux distribution/desktop."""

    system = get_platform_system()
    if system == "Windows":
        release, version, _csd, _ptype = platform.win32_ver()
        # Windows 11 intentionally retains the ``10`` major version for API
        # compatibility; builds 22000 and newer identify it reliably.
        label_release = release or platform.release()
        try:
            build = int(version.split(".")[-1])
        except (IndexError, ValueError):
            build = 0
        if label_release == "10" and build >= 22000:
            label_release = "11"
        label = "Windows {0}".format(label_release)
        return "{0} ({1})".format(label, version) if version else label

    if system == "Linux":
        release = _read_os_release()
        distribution = release.get("PRETTY_NAME") or platform.release()
        desktop = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION")
        if desktop:
            return "Linux - {0} ({1})".format(distribution, desktop)
        return "Linux - {0}".format(distribution)

    details = platform.platform(aliased=True, terse=True)
    return details or system or "unknown"


def get_python_version() -> str:
    """Return the running Python implementation and version."""

    implementation = platform.python_implementation() or "Python"
    return "{0} {1}".format(implementation, platform.python_version())


def _get_windows_memory_bytes() -> Optional[int]:
    """Return installed memory through the Windows API, when available."""

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        memory_status = MemoryStatus()
        memory_status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status)):
            return int(memory_status.ullTotalPhys)
    except (AttributeError, OSError):
        pass
    return None


def get_total_memory_bytes() -> Optional[int]:
    """Return physical memory in bytes, when the platform exposes it."""

    if platform.system() == "Windows":
        return _get_windows_memory_bytes()

    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def get_disk_info(path: Optional[Path] = None) -> Dict[str, object]:
    """Return total and free capacity for the disk containing ``path``."""

    disk_path = (path or Path.cwd()).resolve()
    usage = shutil.disk_usage(str(disk_path))
    return {
        "path": disk_path.anchor or os.sep,
        "total_bytes": usage.total,
        "free_bytes": usage.free,
    }


def get_system_info(path: Optional[Path] = None) -> Dict[str, str]:
    """Return concise, display-ready system information.

    ``path`` selects the disk to report, which makes the result useful for a
    project status as well as straightforward to test.
    """

    memory_bytes = get_total_memory_bytes()
    disk = get_disk_info(path)
    disk_total = int(disk["total_bytes"])
    disk_free = int(disk["free_bytes"])
    free_percent = (disk_free * 100.0 / disk_total) if disk_total else 0.0
    memory = format_bytes(memory_bytes) if memory_bytes is not None else "unavailable"

    return {
        "computer": "{0} ({1})".format(get_computer_name(), get_computer_description()),
        "operating_system": get_operating_system(),
        "python": get_python_version(),
        "memory": memory,
        "disk": "{0}: {1} total, {2} free ({3:.0f} %)".format(
            disk["path"], format_bytes(disk_total), format_bytes(disk_free), free_percent
        ),
    }


def print_system_info(path: Optional[Path] = None) -> None:
    """Print a colored, aligned system overview."""

    terminal = Terminal()
    details = get_system_info(path)
    labels = (
        ("Computer", "computer"),
        ("Operating system", "operating_system"),
        ("Python", "python"),
        ("Memory", "memory"),
        ("Disk", "disk"),
    )
    width = max(len(label) for label, _key in labels) + 1

    print(terminal.color("y", "System information"))
    for label, key in labels:
        rendered_label = "{0}:".format(label).ljust(width)
        print("{0} {1}".format(terminal.color("g", rendered_label), details[key]))
