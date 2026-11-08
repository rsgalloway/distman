#!/usr/bin/env python3
#
# Copyright (c) 2024-2026, Ryan Galloway (ryan@rsgalloway.com)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#  - Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
#  - Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
#  - Neither the name of the software nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#

"""Render transient command activity without changing stable command output."""

import logging
import os
import sys
import threading
from contextlib import contextmanager
from typing import Iterator, Optional, TextIO, Tuple

BRAILLE_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
ASCII_FRAMES = ("|", "/", "-", "\\")
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

UNICODE_STATUS = {
    "info": "•",
    "success": "✓",
    "warning": "!",
    "error": "✗",
    "prompt": "?",
}
ASCII_STATUS = {"info": ">", "success": "+", "warning": "!", "error": "x", "prompt": "?"}


def _environment_flag(name: str) -> bool:
    """Return whether an environment flag contains a conventional true value.

    :param name: Environment variable to inspect.
    :return: True when the variable is present and is not explicitly false.
    """
    value = os.environ.get(name)
    return value is not None and value.strip().lower() not in ("", "0", "false", "no")


def terminal_enabled(stream: TextIO, enabled: bool = True) -> bool:
    """Return whether a stream supports transient interactive presentation.

    :param stream: Output stream whose terminal capability should be inspected.
    :param enabled: Caller-controlled presentation switch.
    :return: True for an enabled interactive terminal outside CI and dumb terminals.
    """
    return (
        enabled
        and callable(getattr(stream, "isatty", None))
        and stream.isatty()
        and os.environ.get("TERM", "").lower() != "dumb"
        and not _environment_flag("CI")
    )


def supports_text(stream: TextIO, value: str) -> bool:
    """Return whether a stream encoding can represent text without replacement.

    :param stream: Output stream advertising an optional encoding.
    :param value: Text whose encoding support should be tested.
    :return: True when the configured encoding accepts the text.
    """
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        value.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def format_status(message: str, status: str, stream: TextIO) -> str:
    """Prefix one interactive terminal line with a semantic status marker.

    :param message: User-facing line to format.
    :param status: Semantic marker name such as info, success, warning, error, or prompt.
    :param stream: Output stream controlling terminal, encoding, and color behavior.
    :return: Terminal-formatted line, or unchanged text for noninteractive output.
    """
    if not terminal_enabled(stream):
        return message
    symbols = UNICODE_STATUS if supports_text(stream, "•✓✗") else ASCII_STATUS
    selected = status if status in symbols else "info"
    symbol = symbols[selected]
    color = {
        "success": GREEN,
        "warning": YELLOW,
        "error": RED,
        "prompt": YELLOW,
    }.get(selected, "")
    if "NO_COLOR" not in os.environ and color:
        symbol = f"{color}{symbol}{RESET}"
    return f"{symbol} {message}"


class StatusFormatter(logging.Formatter):
    """Prefix interactive console records with aligned semantic status markers.

    :param stream: Console stream used to determine terminal and encoding support.
    """

    def __init__(self, stream: TextIO) -> None:
        """Initialize terminal, color, and marker capabilities.

        :param stream: Console stream used to determine terminal and encoding support.
        """
        super().__init__("%(message)s")
        self.stream = stream
        self.enabled = terminal_enabled(stream)
        self.symbols = UNICODE_STATUS if supports_text(stream, "•✓✗") else ASCII_STATUS

    def format(self, record: logging.LogRecord) -> str:
        """Format one record with a marker on every output line when interactive.

        :param record: Log record containing an optional semantic status value.
        :return: Plain or terminal-formatted message.
        """
        message = super().format(record)
        if not self.enabled:
            return message
        status = getattr(record, "status", None)
        if status not in self.symbols:
            status = (
                "error"
                if record.levelno >= logging.ERROR
                else "warning" if record.levelno >= logging.WARNING else "info"
            )
        return "\n".join(
            format_status(line, status, self.stream) for line in message.splitlines() or [""]
        )


class Spinner:
    """Render a single transient activity line on an interactive terminal.

    :param enabled: Whether the caller permits animated output.
    :param stream: Terminal stream receiving transient output; defaults to stderr.
    :param interval: Delay in seconds between animation frames.
    """

    def __init__(
        self,
        enabled: bool = True,
        stream: Optional[TextIO] = None,
        interval: float = 0.08,
    ) -> None:
        """Initialize terminal capability detection and animation state.

        :param enabled: Whether the caller permits animated output.
        :param stream: Terminal stream receiving transient output; defaults to stderr.
        :param interval: Delay in seconds between animation frames.
        """
        self.stream = stream or sys.stderr
        self.interval = interval
        self.enabled = terminal_enabled(self.stream, enabled)
        self.color = self.enabled and "NO_COLOR" not in os.environ
        self.frames = self._frames()
        self._frame = 0
        self._last_width = 0
        self._message = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _frames(self) -> Tuple[str, ...]:
        """Choose Unicode frames when the output encoding can represent them.

        :return: Braille animation frames, or an ASCII fallback.
        """
        return (
            BRAILLE_FRAMES if supports_text(self.stream, "".join(BRAILLE_FRAMES)) else ASCII_FRAMES
        )

    def _write(self, value: str) -> None:
        """Write and flush one transient terminal update.

        :param value: Text or control characters to write.
        :return: None.
        """
        try:
            self.stream.write(value)
            self.stream.flush()
        except (OSError, UnicodeError):
            self.enabled = False

    def _render(self) -> None:
        """Render the current frame and message while holding the state lock.

        :return: None.
        """
        if not self.enabled:
            return
        raw_frame = self.frames[self._frame]
        line = f"{self._style(raw_frame, YELLOW)} {self._message}"
        visible_width = len(raw_frame) + 1 + len(self._message)
        padding = " " * max(0, self._last_width - visible_width)
        self._write(f"\r{line}{padding}")
        self._last_width = visible_width
        self._frame = (self._frame + 1) % len(self.frames)

    def _style(self, value: str, color: str) -> str:
        """Apply one ANSI color when terminal color is enabled.

        :param value: Visible symbol to color.
        :param color: ANSI foreground color sequence.
        :return: Colored symbol, or the unchanged symbol in plain mode.
        """
        return f"{color}{value}{RESET}" if self.color else value

    def _animate(self) -> None:
        """Advance frames until the active operation signals completion.

        :return: None.
        """
        while not self._stop.wait(self.interval):
            with self._lock:
                self._render()

    def start(self, message: str) -> None:
        """Start rendering one activity message.

        :param message: Human-readable description of the active operation.
        :return: None.
        :raises RuntimeError: If another activity is already running.
        """
        if not self.enabled:
            return
        if self._thread is not None:
            raise RuntimeError("Spinner activity is already running")
        self._message = message
        self._frame = 0
        self._stop.clear()
        with self._lock:
            self._render()
        self._thread = threading.Thread(target=self._animate, name="distman-spinner", daemon=True)
        self._thread.start()

    def update(self, message: str) -> None:
        """Replace the active phase description and refresh it immediately.

        :param message: Human-readable description of the new phase.
        :return: None.
        """
        if not self.enabled or self._thread is None:
            return
        with self._lock:
            self._message = message
            self._render()

    def stop(self) -> None:
        """Stop animation and erase the transient line.

        :return: None.
        """
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join()
        self._thread = None
        with self._lock:
            self._write("\r" + (" " * self._last_width) + "\r")
            self._last_width = 0

    def finish(self, message: str, success: bool = True) -> None:
        """Clear active animation and write one stable colored result line.

        :param message: Human-readable result description.
        :param success: Whether to render a green success or red failure marker.
        :return: None.
        """
        active = self._thread is not None
        self.stop()
        if active:
            unicode_output = self.frames == BRAILLE_FRAMES
            marker_text = ("✓" if success else "✗") if unicode_output else ("+" if success else "x")
            marker = self._style(marker_text, GREEN if success else RED)
            self._write(f"{marker} {message}\n")

    @contextmanager
    def activity(
        self,
        message: str,
        success: Optional[str] = None,
        failure: Optional[str] = None,
    ) -> Iterator["Spinner"]:
        """Run an operation with a transient activity line and guaranteed cleanup.

        :param message: Initial human-readable phase description.
        :param success: Stable result shown after successful completion.
        :param failure: Stable result shown after exceptional completion.
        :return: Context iterator yielding this spinner for phase updates.
        """
        self.start(message)
        try:
            yield self
        except BaseException:
            self.finish(failure or f"{message} failed", success=False)
            raise
        else:
            self.finish(success or message)
