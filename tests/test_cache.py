#!/usr/bin/env python3
#
# Copyright (c) 2024-2025, Ryan Galloway (ryan@rsgalloway.com)
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

__doc__ = """
Contains tests for the cache module.
"""

import time
from pathlib import Path

from distman import cache


def test_ttl_expired_no_last_check(tmp_path):
    assert cache._ttl_expired(tmp_path, ttl=60) is True


def test_ttl_not_expired(tmp_path):
    p = cache._last_check_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(str(time.time()))
    assert cache._ttl_expired(tmp_path, ttl=60) is False


def test_ttl_zero_always_expired(tmp_path):
    assert cache._ttl_expired(tmp_path, ttl=0) is True


def write_epoch(root: Path, value: str):
    (root / ".distman").mkdir(exist_ok=True)
    (root / ".distman" / "epoch").write_text(value)


def test_cache_stale_when_epochs_differ(tmp_path):
    deploy = tmp_path / "deploy"
    cache_root = tmp_path / "cache"
    deploy.mkdir()
    cache_root.mkdir()

    write_epoch(deploy, "123")
    write_epoch(cache_root, "456")

    assert cache._read_deploy_epoch(deploy) != cache._read_cache_epoch(cache_root)


def test_cache_fresh_when_epochs_match(tmp_path):
    deploy = tmp_path / "deploy"
    cache_root = tmp_path / "cache"
    deploy.mkdir()
    cache_root.mkdir()

    write_epoch(deploy, "123")
    write_epoch(cache_root, "123")

    assert cache._read_deploy_epoch(deploy) == cache._read_cache_epoch(cache_root)


def test_check_returns_stale_exit_code(tmp_path, monkeypatch):
    deploy = tmp_path / "deploy"
    cache_root = tmp_path / "cache"
    deploy.mkdir()
    cache_root.mkdir()

    write_epoch(deploy, "1")
    write_epoch(cache_root, "0")

    args = cache.build_parser().parse_args(
        [
            "--src",
            str(deploy),
            "--dst",
            str(cache_root),
            "--check",
            "--ttl",
            "0",
        ]
    )

    rc = cache.run(args)
    assert rc == cache.STALE_EXIT


def test_check_returns_zero_when_fresh(tmp_path):
    deploy = tmp_path / "deploy"
    cache_root = tmp_path / "cache"
    deploy.mkdir()
    cache_root.mkdir()

    write_epoch(deploy, "1")
    write_epoch(cache_root, "1")

    args = cache.build_parser().parse_args(
        [
            "--src",
            str(deploy),
            "--dst",
            str(cache_root),
            "--check",
            "--ttl",
            "0",
        ]
    )

    assert cache.run(args) == 0


def test_clone_not_called_when_fresh(tmp_path, monkeypatch):
    deploy = tmp_path / "deploy"
    cache_root = tmp_path / "cache"
    deploy.mkdir()
    cache_root.mkdir()

    write_epoch(deploy, "1")
    write_epoch(cache_root, "1")

    called = False

    def fake_clone(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(cache, "clone", fake_clone)

    args = cache.build_parser().parse_args(
        [
            "--src",
            str(deploy),
            "--dst",
            str(cache_root),
            "--ttl",
            "0",
        ]
    )

    cache.run(args)
    assert called is False


def test_epoch_written_after_clone(tmp_path, monkeypatch):
    deploy = tmp_path / "deploy"
    cache_root = tmp_path / "cache"
    deploy.mkdir()
    cache_root.mkdir()

    print("-" * 20)
    write_epoch(deploy, "999")

    monkeypatch.setattr(cache, "clone", lambda *a, **k: None)

    args = cache.build_parser().parse_args(
        [
            "--src",
            str(deploy),
            "--dst",
            str(cache_root),
            "--ttl",
            "0",
        ]
    )

    cache.run(args)

    value = cache._read_cache_epoch(cache_root)
    print("cache_root", cache_root)
    print("value", value)
    print("-" * 20)
    assert value == "999"


def test_diff_ignores_pycache(tmp_path, caplog):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("x")

    diff = cache.diff_trees(src, dst)
    assert diff == 0
