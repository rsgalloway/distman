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

"""Explicit recovery command for interrupted journaled publications."""

import argparse
from pathlib import Path

from distman import util
from distman.deployment import recover_publication
from distman.locking import deployment_lock
from distman.logger import log


def recover(args: argparse.Namespace) -> int:
    """Inspect or restore pending publication under the deployment writer lock.

    :param args: Explicit root, dry-run, and confirmation options.
    :return: Zero on success, one on failure/decline, or 130 on cancellation.
    """
    root = Path(args.root).expanduser().resolve()
    try:
        if not root.is_dir():
            raise ValueError(f"Deployment root does not exist: {root}")
        with deployment_lock(root, dryrun=args.dryrun):
            actions = recover_publication(root, dryrun=True)
            if not actions:
                log.info("No pending publication at %s", root)
                return 0
            for action in actions:
                log.info("Planned recovery: %s", action)
            if args.dryrun:
                log.info("Dry run: deployment unchanged")
                return 0
            if not args.yes and not util.yesNo(f"Recover publication at {root}?"):
                return 1
            recover_publication(root)
            log.info("Recovered publication at %s", root)
            return 0
    except KeyboardInterrupt:
        log.error("Recovery canceled; inspect pending publication before continuing")
        return 130
    except (OSError, ValueError) as exc:
        log.error("Recovery failed: %s", exc)
        return 1
