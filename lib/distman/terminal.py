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


def _environment_flag(name: str) -> bool:
    """Return whether an environment flag contains a conventional true value.

    :param name: Environment variable to inspect.
    :return: True when the variable is present and is not explicitly false.
    """
    value = os.environ.get(name)
    return value is not None and value.strip().lower() not in ("", "0", "false", "no")


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
        self.enabled = (
            enabled
            and callable(getattr(self.stream, "isatty", None))
            and self.stream.isatty()
            and os.environ.get("TERM", "").lower() != "dumb"
            and not _environment_flag("CI")
        )
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
        encoding = getattr(self.stream, "encoding", None) or "utf-8"
        try:
            "".join(BRAILLE_FRAMES).encode(encoding)
        except (LookupError, UnicodeEncodeError):
            return ASCII_FRAMES
        return BRAILLE_FRAMES

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
