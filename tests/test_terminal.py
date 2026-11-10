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

"""Tests for transient terminal activity rendering."""

import io
import logging
from typing import Optional

import pytest

import distman.terminal as terminal_module
from distman.terminal import ASCII_FRAMES, BRAILLE_FRAMES, Spinner, StatusFormatter, format_status


class Terminal(io.StringIO):
    """In-memory stream with configurable terminal and encoding behavior."""

    def __init__(self, terminal: bool = True, encoding: str = "utf-8") -> None:
        """Initialize the test stream.

        :param terminal: Value returned by isatty.
        :param encoding: Encoding advertised to the renderer.
        """
        super().__init__()
        self.terminal = terminal
        self.test_encoding = encoding

    @property
    def encoding(self) -> str:
        """Return the configured output encoding.

        :return: Encoding name used for frame selection.
        """
        return self.test_encoding

    def isatty(self) -> bool:
        """Return whether the stream represents an interactive terminal.

        :return: Configured terminal capability.
        """
        return self.terminal


def test_status_formatter_aligns_and_colors_console_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefix interactive log lines with semantic colored markers.

    :param monkeypatch: Fixture enabling terminal presentation outside CI.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(terminal_module, "color_enabled", lambda stream: True)
    formatter = StatusFormatter(Terminal())

    success = logging.LogRecord("distman", logging.INFO, "", 0, "Updated\nInstalled", (), None)
    success.status = "success"
    addition = logging.LogRecord("distman", logging.INFO, "", 0, "New package", (), None)
    addition.status = "addition"
    removal = logging.LogRecord("distman", logging.INFO, "", 0, "Planned removal", (), None)
    removal.status = "removal"
    warning = logging.LogRecord("distman", logging.WARNING, "", 0, "Not on main", (), None)
    error = logging.LogRecord("distman", logging.ERROR, "", 0, "Failed", (), None)

    assert formatter.format(success) == ("\033[32m✓\033[0m Updated\n\033[32m✓\033[0m Installed")
    assert formatter.format(addition) == "\033[32m+\033[0m New package"
    assert formatter.format(removal) == "\033[31m−\033[0m Planned removal"
    assert formatter.format(warning) == "\033[33m!\033[0m Not on main"
    assert formatter.format(error) == "\033[31m✗\033[0m Failed"


def test_format_status_marks_interactive_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a yellow question marker for an interactive confirmation prompt.

    :param monkeypatch: Fixture enabling terminal presentation outside CI.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(terminal_module, "color_enabled", lambda stream: True)

    assert format_status("Continue? [Y/n]:", "prompt", Terminal()) == (
        "[33m?[0m Continue? [Y/n]:"
    )


@pytest.mark.parametrize("supported", [False, True])
def test_windows_color_requires_virtual_terminal_support(
    monkeypatch: pytest.MonkeyPatch, supported: bool
) -> None:
    """Emit ANSI color on Windows only when virtual-terminal processing is available.

    :param monkeypatch: Fixture simulating Windows console capability.
    :param supported: Whether virtual-terminal processing can be enabled.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(terminal_module, "WINDOWS", True)
    monkeypatch.setattr(
        terminal_module,
        "_enable_windows_virtual_terminal",
        lambda stream: supported,
    )

    formatted = format_status("Updated", "success", Terminal())

    assert formatted == ("\033[32m✓\033[0m Updated" if supported else "✓ Updated")


def test_status_formatter_preserves_redirected_output() -> None:
    """Keep redirected output free of terminal markers and ANSI sequences.

    :return: None.
    """
    formatter = StatusFormatter(Terminal(terminal=False))
    record = logging.LogRecord("distman", logging.INFO, "", 0, "Updated", (), None)
    record.status = "success"

    assert formatter.format(record) == "Updated"


def test_status_formatter_uses_ascii_removal_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a portable removal marker when the terminal cannot encode Unicode.

    :param monkeypatch: Fixture enabling terminal presentation outside CI.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    formatter = StatusFormatter(Terminal(encoding="ascii"))
    record = logging.LogRecord("distman", logging.INFO, "", 0, "Planned removal", (), None)
    record.status = "removal"

    assert formatter.format(record) == "- Planned removal"


def test_spinner_updates_and_clears_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render phase changes and erase transient content when the activity exits.

    :param monkeypatch: Fixture removing environment settings that disable animation.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    stream = Terminal()
    spinner = Spinner(stream=stream, interval=60)

    with spinner.activity("Resolving packages", success="Downloaded wheels"):
        spinner.update("Downloading 2 wheels")

    output = stream.getvalue()
    assert BRAILLE_FRAMES[0] + " Resolving packages" in output
    assert BRAILLE_FRAMES[1] + " Downloading 2 wheels" in output
    assert "\r" + (" " * len("⠙ Downloading 2 wheels")) + "\r" in output
    assert output.endswith("✓ Downloaded wheels\n")
    assert "\033[" not in output


def test_spinner_colors_active_and_success_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use yellow for activity and green for successful completion.

    :param monkeypatch: Fixture enabling terminal animation outside CI.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(terminal_module, "color_enabled", lambda stream: True)
    stream = Terminal()

    with Spinner(stream=stream, interval=60).activity("Resolving", success="Resolved"):
        pass

    output = stream.getvalue()
    assert "\033[33m⠋\033[0m Resolving" in output
    assert output.endswith("\033[32m✓\033[0m Resolved\n")


@pytest.mark.parametrize("terminal,ci", [(False, None), (True, "1")])
def test_spinner_is_silent_without_interactive_terminal(
    monkeypatch: pytest.MonkeyPatch, terminal: bool, ci: Optional[str]
) -> None:
    """Do not emit control output for redirected streams or CI jobs.

    :param monkeypatch: Fixture configuring CI behavior.
    :param terminal: Whether the stream claims to be interactive.
    :param ci: Optional CI environment flag.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    if ci:
        monkeypatch.setenv("CI", ci)
    stream = Terminal(terminal=terminal)

    with Spinner(stream=stream).activity("Resolving packages"):
        pass

    assert stream.getvalue() == ""


def test_spinner_uses_ascii_for_limited_encodings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fall back to portable frames when the terminal cannot encode Braille glyphs.

    :param monkeypatch: Fixture removing environment settings that disable animation.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    stream = Terminal(encoding="ascii")
    spinner = Spinner(stream=stream, interval=60)

    with spinner.activity("Building wheel"):
        pass

    assert spinner.frames == ASCII_FRAMES
    assert ASCII_FRAMES[0] + " Building wheel" in stream.getvalue()
    assert stream.getvalue().endswith("+ Building wheel\n")


def test_spinner_cleans_up_after_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the animation thread and clear its line when an operation fails.

    :param monkeypatch: Fixture removing environment settings that disable animation.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(terminal_module, "color_enabled", lambda stream: True)
    stream = Terminal()
    spinner = Spinner(stream=stream, interval=60)

    with pytest.raises(RuntimeError):
        with spinner.activity("Publishing"):
            raise RuntimeError("failed")

    assert spinner._thread is None
    assert "\033[33m⠋\033[0m Publishing" in stream.getvalue()
    assert stream.getvalue().endswith("\033[31m✗\033[0m Publishing failed\n")


def test_spinner_ignores_closed_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable optional presentation when its output stream is unavailable.

    :param monkeypatch: Fixture enabling terminal presentation outside CI.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    stream = Terminal()
    stream.close()
    spinner = Spinner(stream=stream)

    with spinner.activity("Preparing packages"):
        pass

    assert spinner.enabled is False
    assert spinner._thread is None


def test_spinner_ignores_thread_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Continue the operation when the optional animation thread cannot start.

    :param monkeypatch: Fixture injecting a thread startup failure.
    :return: None.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    stream = Terminal()
    spinner = Spinner(stream=stream)

    def fail_start(thread: object) -> None:
        """Raise the simulated thread startup failure.

        :param thread: Animation thread that cannot be started.
        :return: None.
        :raises RuntimeError: Always.
        """
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(terminal_module.threading.Thread, "start", fail_start)

    with spinner.activity("Preparing packages"):
        pass

    assert spinner.enabled is False
    assert spinner._thread is None
    assert stream.getvalue() == ""
