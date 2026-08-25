"""Portable, dependency-free terminal colors for small command-line tools."""

import os
import re
import shutil
import sys
from typing import Optional, TextIO


__version__ = "0.25.01"


RESET = "\033[0m"
COLORS = {
    # Standard ANSI colors
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    # Bright ANSI colors
    "bright_black": "\033[90m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
    "bright_white": "\033[97m",
}

COLOR_ALIASES = {
    "r": "red",
    "g": "green",
    "b": "blue",
    "y": "bright_yellow",
    "w": "bright_white",
    "m": "magenta",
    "c": "cyan",
    "v": "bright_magenta",  # violet
    "violet": "bright_magenta",
}

BACKGROUND_COLORS = {
    name: f"\033[{int(value[2:-1]) + 10}m" for name, value in COLORS.items()
}

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SPINNER_FRAMES = "|/-\\"


def _enable_windows_ansi(stream: TextIO) -> bool:
    """Enable ANSI output on supported Windows consoles without dependencies."""

    if os.name != "nt":
        return True

    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint()
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError, ValueError):
        return False


def ansi_enabled(stream: Optional[TextIO] = None) -> bool:
    """Return whether the selected stream supports ANSI control sequences."""

    output = stream or sys.stdout
    if not output.isatty():
        return False
    return _enable_windows_ansi(output)


def colors_enabled(stream: Optional[TextIO] = None) -> bool:
    """Return whether ANSI colors should be used for the selected stream."""

    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") not in {None, "", "0"}:
        return True
    return ansi_enabled(stream)


def _resolve_color(color: str) -> str:
    """Return the ANSI sequence for a color name or its short alias."""

    original_color = color
    color = COLOR_ALIASES.get(color, color)
    if color not in COLORS:
        raise ValueError(f"Unknown terminal color: {original_color}")
    return COLORS[color]


def style_text(
    text: object,
    *,
    fg: Optional[str] = None,
    bg: Optional[str] = None,
    bold: bool = False,
    dim: bool = False,
    underline: bool = False,
    enabled: Optional[bool] = None,
) -> str:
    """Return text with optional ANSI foreground, background, and text styles."""

    use_ansi = colors_enabled() if enabled is None else enabled
    value = str(text)
    foreground = _resolve_color(fg) if fg is not None else None
    background = None
    if bg is not None:
        background_name = COLOR_ALIASES.get(bg, bg)
        if background_name not in BACKGROUND_COLORS:
            raise ValueError(f"Unknown terminal background color: {bg}")
        background = BACKGROUND_COLORS[background_name]
    if not use_ansi:
        return value

    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if underline:
        codes.append("4")
    if foreground is not None:
        codes.append(foreground[2:-1])
    if background is not None:
        codes.append(background[2:-1])

    if not codes:
        return value
    return f"\033[{';'.join(codes)}m{value}{RESET}"


def color_text(text: object, color: str, *, enabled: Optional[bool] = None) -> str:
    """Return text in the requested color, or plain text when colors are disabled."""

    return style_text(text, fg=color, enabled=enabled)


class Terminal:
    """Print colored terminal text through short color methods."""

    def __init__(self, file: Optional[TextIO] = None) -> None:
        self._file = file

    def print(
        self,
        color: str,
        *values: object,
        sep: str = " ",
        end: str = "\n",
    ) -> None:
        """Print values in a named terminal color or a short color alias."""

        output = self._file or sys.stdout
        text = sep.join(str(value) for value in values)
        print(color_text(text, color, enabled=colors_enabled(output)), end=end, file=output)

    def color(self, color: str, text: object) -> str:
        """Return one piece of text in color for use inside a longer line."""

        output = self._file or sys.stdout
        return color_text(text, color, enabled=colors_enabled(output))

    def style(
        self,
        text: object,
        *,
        fg: Optional[str] = None,
        bg: Optional[str] = None,
        bold: bool = False,
        dim: bool = False,
        underline: bool = False,
    ) -> str:
        """Return styled text for use inside a longer line."""

        output = self._file or sys.stdout
        return style_text(
            text,
            fg=fg,
            bg=bg,
            bold=bold,
            dim=dim,
            underline=underline,
            enabled=colors_enabled(output),
        )

    def r(self, *values: object, **kwargs: object) -> None:
        """Print in red."""

        self.print("r", *values, **kwargs)

    def g(self, *values: object, **kwargs: object) -> None:
        """Print in green."""

        self.print("g", *values, **kwargs)

    def b(self, *values: object, **kwargs: object) -> None:
        """Print in blue."""

        self.print("b", *values, **kwargs)

    def y(self, *values: object, **kwargs: object) -> None:
        """Print in bright yellow/orange."""

        self.print("y", *values, **kwargs)

    def w(self, *values: object, **kwargs: object) -> None:
        """Print in white."""

        self.print("w", *values, **kwargs)

    def m(self, *values: object, **kwargs: object) -> None:
        """Print in magenta."""

        self.print("m", *values, **kwargs)

    def c(self, *values: object, **kwargs: object) -> None:
        """Print in cyan."""

        self.print("c", *values, **kwargs)

    def v(self, *values: object, **kwargs: object) -> None:
        """Print in bright magenta/violet."""

        self.print("v", *values, **kwargs)


def r(*values: object, **kwargs: object) -> None:
    """Print in red."""

    Terminal().r(*values, **kwargs)


def g(*values: object, **kwargs: object) -> None:
    """Print in green."""

    Terminal().g(*values, **kwargs)


def b(*values: object, **kwargs: object) -> None:
    """Print in blue."""

    Terminal().b(*values, **kwargs)


def y(*values: object, **kwargs: object) -> None:
    """Print in bright yellow/orange."""

    Terminal().y(*values, **kwargs)


def w(*values: object, **kwargs: object) -> None:
    """Print in white."""

    Terminal().w(*values, **kwargs)


def m(*values: object, **kwargs: object) -> None:
    """Print in magenta."""

    Terminal().m(*values, **kwargs)


def c(*values: object, **kwargs: object) -> None:
    """Print in cyan."""

    Terminal().c(*values, **kwargs)


def v(*values: object, **kwargs: object) -> None:
    """Print in bright magenta/violet."""

    Terminal().v(*values, **kwargs)


def strip_ansi(text: object) -> str:
    """Return text without ANSI CSI control sequences."""

    return ANSI_ESCAPE_RE.sub("", str(text))


def terminal_width(default: int = 80) -> int:
    """Return the terminal width, or ``default`` when it cannot be detected."""

    if default < 1:
        raise ValueError("Terminal width default must be positive")
    return shutil.get_terminal_size(fallback=(default, 24)).columns


def progress_bar(
    current: int,
    total: int,
    width: Optional[int] = 30,
    *,
    complete: str = "#",
    empty: str = "-",
) -> str:
    """Return a text progress bar, clamping progress to the range 0 through total."""

    if total <= 0:
        raise ValueError("Progress total must be positive")
    if width is None:
        width = max(10, terminal_width() - 30)
    if width < 1:
        raise ValueError("Progress bar width must be positive")
    if len(complete) != 1 or len(empty) != 1:
        raise ValueError("Progress bar characters must each have length one")

    shown_current = min(max(current, 0), total)
    ratio = shown_current / total
    filled = round(width * ratio)
    bar = complete * filled + empty * (width - filled)
    return f"[{bar}] {ratio:>6.1%} ({shown_current}/{total})"


def spinner(frame: int, frames: str = SPINNER_FRAMES) -> str:
    """Return one spinner character, selected cyclically by ``frame``."""

    if not frames:
        raise ValueError("Spinner frames must not be empty")
    return frames[frame % len(frames)]


def _write_control(sequence: str, stream: Optional[TextIO] = None) -> bool:
    """Write an ANSI control sequence when the target stream supports it."""

    output = stream or sys.stdout
    if not ansi_enabled(output):
        return False
    print(sequence, end="", file=output, flush=True)
    return True


def clear_line(stream: Optional[TextIO] = None) -> None:
    """Clear the current terminal line and move the cursor to its start."""

    _write_control("\r\033[K", stream)


def cursor_up(count: int = 1, stream: Optional[TextIO] = None) -> None:
    """Move the cursor up by ``count`` rows."""

    _move_cursor("A", count, stream)


def cursor_down(count: int = 1, stream: Optional[TextIO] = None) -> None:
    """Move the cursor down by ``count`` rows."""

    _move_cursor("B", count, stream)


def _move_cursor(direction: str, count: int, stream: Optional[TextIO]) -> None:
    """Move the cursor in one ANSI direction after validating the row count."""

    if count < 1:
        raise ValueError("Cursor movement count must be positive")
    _write_control(f"\033[{count}{direction}", stream)


def hide_cursor(stream: Optional[TextIO] = None) -> None:
    """Hide the terminal cursor when ANSI control sequences are supported."""

    _write_control("\033[?25l", stream)


def show_cursor(stream: Optional[TextIO] = None) -> None:
    """Show the terminal cursor when ANSI control sequences are supported."""

    _write_control("\033[?25h", stream)


class StatusLine:
    """Keep one dynamic status line below regular log messages."""

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        self._stream = stream
        self._text: Optional[str] = None
        self._active = False

    @property
    def stream(self) -> TextIO:
        """Return the stream selected for this status line."""

        return self._stream or sys.stdout

    def update(self, text: object) -> None:
        """Draw ``text`` as the current status line."""

        self._text = str(text)
        output = self.stream
        if ansi_enabled(output):
            print(f"\r{self._text}\033[K", end="", file=output, flush=True)
            self._active = True
        else:
            print(self._text, file=output, flush=True)
            self._active = False

    def clear(self) -> None:
        """Remove the active status text without printing a newline."""

        if self._active:
            clear_line(self.stream)
            self._active = False

    def log(self, *values: object, sep: str = " ", end: str = "\n") -> None:
        """Print a regular message above the active status line."""

        previous_text = self._text
        was_active = self._active
        if was_active:
            self.clear()
        print(*values, sep=sep, end=end, file=self.stream, flush=True)
        if was_active and previous_text is not None:
            self.update(previous_text)

    def finish(self, text: Optional[object] = None) -> None:
        """Optionally update the status, then finalize it with a newline."""

        if text is not None:
            self.update(text)
        if self._active:
            print(file=self.stream, flush=True)
        self._active = False
        self._text = None


def status_line(text: object) -> None:
    """Replace the current terminal line with ``text`` without adding a newline."""

    output = sys.stdout
    if ansi_enabled(output):
        print(f"\r{text}\033[K", end="", file=output, flush=True)
    else:
        print(text, file=output, flush=True)


def t100(*values: object, **kwargs: object) -> None:
    """Backward-compatible name for green output; prefer ``g(...)``."""

    g(*values, **kwargs)
