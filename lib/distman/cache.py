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
Contains cross-platform directory cloning utilities.
"""

import argparse
import concurrent.futures as cf
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from tqdm import tqdm
from typing import List, Optional, Sequence, Tuple, Set

from distman import config, util
from distman.logger import log, setup_logging

# exit code when cache is stale
STALE_EXIT = 10


def _cache_meta_dir(cache_root: Path) -> Path:
    """Get the .distman metadata directory under cache_root."""
    return cache_root / ".distman"


def _last_check_path(cache_root: Path) -> Path:
    """Get the path to the last_check file under cache metadata."""
    return _cache_meta_dir(cache_root) / "last_check"


def _ttl_expired(cache_root: Path, ttl: float) -> bool:
    """Check if the TTL has expired since the last check.

    :param cache_root: Root of the cache directory.
    :param ttl: Time-to-live in seconds.
    :return: True if TTL has expired or no last check recorded, False otherwise.
    """
    if ttl <= 0:
        return True

    p = _last_check_path(cache_root)
    try:
        last = float(p.read_text().strip())
    except Exception:
        return True

    return (time.time() - last) >= ttl


def _mark_checked(cache_root: Path) -> None:
    """Mark the current time as the last check time."""
    p = _last_check_path(cache_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{time.time()}\n", encoding="utf-8")


def is_windows() -> bool:
    """Check if the current operating system is Windows."""
    return os.name == "nt"


def norm_rel(root: Path, p: Path) -> Path:
    """Normalize path p relative to root.

    :param root: Root directory.
    :param p: Path to normalize.
    :return: Relative path from root to p.
    """
    return p.relative_to(root)


def file_signature(p: Path) -> Tuple[int, int]:
    """Get file size and modification time in nanoseconds.

    :param p: Path to the file.
    :return: Tuple of (size in bytes, modification time in nanoseconds).
    """
    st = p.stat()
    return (st.st_size, st.st_mtime_ns)


def ensure_dir(p: Path) -> None:
    """Ensure that directory p exists.

    :param p: Directory path to ensure
    """
    p.mkdir(parents=True, exist_ok=True)


def atomic_replace(src_tmp: Path, dst: Path) -> None:
    """Atomically replace dst with src_tmp.

    :param src_tmp: Temporary source file path.
    :param dst: Destination file path.
    """
    os.replace(src_tmp, dst)


# TODO: consolidate with util.check_symlinks
def can_create_symlinks(test_dir: Path) -> bool:
    """Check if symlinks can be created in the given directory.

    :param test_dir: Directory to test symlink creation.
    :return: True if symlinks can be created, False otherwise.
    """
    try:
        ensure_dir(test_dir)
        target = test_dir / ".symlink_target_tmp"
        link = test_dir / ".symlink_link_tmp"
        target.write_text("x", encoding="utf-8")
        os.symlink(target, link, target_is_directory=False)
        link.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        return True
    except Exception:
        for f in (link, target):
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass
        return False


def create_symlink(src_link: Path, dst_link: Path, dst_supports_symlinks: bool) -> bool:
    """Create a symlink at dst_link pointing to the same target as src_link.

    :param src_link: Source symlink path.
    :param dst_link: Destination symlink path.
    :param dst_supports_symlinks: Whether the destination supports symlinks.
    :return: True if symlink was created, False otherwise.
    """
    if not dst_supports_symlinks:
        return False
    link_target_text = os.readlink(src_link)
    target_is_dir = False
    try:
        resolved = src_link.parent / link_target_text
        target_is_dir = resolved.is_dir()
    except Exception:
        target_is_dir = False
    if dst_link.exists() or dst_link.is_symlink():
        if dst_link.is_dir() and not dst_link.is_symlink():
            shutil.rmtree(dst_link)
        else:
            dst_link.unlink(missing_ok=True)
    os.symlink(
        link_target_text,
        dst_link,
        target_is_directory=target_is_dir if is_windows() else False,
    )
    return True


def same_file(src: Path, dst: Path) -> bool:
    """Check if two files are the same based on size and modification time.

    :param src: Source file path.
    :param dst: Destination file path.
    :return: True if files are the same, False otherwise.
    """
    try:
        s1, m1 = file_signature(src)
        s2, m2 = file_signature(dst)
        return s1 == s2 and m1 == m2
    except FileNotFoundError:
        return False


def copy_file_task(src: Path, dst: Path) -> str:
    """Copy a single file from src to dst atomically.

    :param src: Source file path.
    :param dst: Destination file path.
    :return: "copied", "skip", or "error:<message>".
    """
    try:
        if dst.exists() and same_file(src, dst):
            return "skip"
        ensure_dir(dst.parent)
        with tempfile.NamedTemporaryFile(delete=False, dir=str(dst.parent)) as tf:
            tmp_path = Path(tf.name)
        try:
            shutil.copy2(src, tmp_path)
            atomic_replace(tmp_path, dst)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return "copied"
    except Exception as e:
        return f"error:{e}"


def collapse_dirs(dirs: Set[Path], root: Path) -> Set[Path]:
    """If a directory is present, drop all children under it.

    :param dirs: Set of relative paths.
    :param root: Root directory to check for directories.
    :return: Collapsed set of missing relative paths.
    """

    # identify which missing paths are dirs in the tree we are reporting from
    paths = set()
    for rel in dirs:
        try:
            if (root / rel).is_dir():
                paths.add(rel)
        except Exception:
            # ignore errors
            pass

    if not paths:
        return dirs

    # keep rel only if no missing dir is a parent of rel (excluding itself)
    collapsed = set()
    for rel in dirs:
        parent = rel.parent
        skip = False
        while parent != parent.parent:  # until '.'
            if parent in paths:
                skip = True
                break
            if parent == Path("."):
                break
            parent = parent.parent
        if not skip:
            collapsed.add(rel)
    return collapsed


def copy_tree_fallback(
    src_dir: Path, dst_dir: Path, executor: cf.Executor, results: list
) -> None:
    """Fallback copy tree that does not handle symlinks.

    :param src_dir: Source directory.
    :param dst_dir: Destination directory.
    :param executor: Executor to submit copy tasks to.
    """
    for root, dirs, files in os.walk(src_dir, followlinks=False):
        root_p = Path(root)
        rel = root_p.relative_to(src_dir)
        ensure_dir(dst_dir / rel)
        for f in files:
            s = root_p / f
            d = dst_dir / rel / f
            results.append(executor.submit(copy_file_task, s, d))


def diff_sort_key(rel: Path):
    """Sort key for diffing paths, prioritizing versioned files.

    :param rel: Relative path to analyze.
    :return: Tuple for sorting: (parent_path, name, version, commit, full_rel_path)
    """
    parts = rel.parts
    if "versions" in parts:
        i = parts.index("versions")
        if i + 1 < len(parts):
            obj = parts[i + 1]
            parsed = util.parse_versioned_filename(obj, obj.split(".")[0])
            if parsed:
                name, ver, commit = parsed
                parent = Path(*parts[: i + 1])  # path up to ".../versions"
                return (str(parent), name, ver, commit, str(rel))
    return ("", "", -1, "", str(rel))


def diff_trees(
    src_root: Path = config.DEPLOY_ROOT, dst_root: Path = config.CACHE_ROOT
) -> int:
    """Compare src and dst recursively. Returns number of differences.

    :param src_root: Source directory.
    :param dst_root: Destination directory.
    :return: Number of differences found.
    """
    differences = 0
    log.debug(f"comparing {src_root} -> {dst_root}")

    src_entries: Set[Path] = set()
    dst_entries: Set[Path] = set()

    for root, dirs, files in os.walk(src_root, followlinks=False):
        rp = Path(root)
        rel_root = norm_rel(src_root, rp)

        # If the directory itself is ignorable, skip it entirely
        if util.is_ignorable(str(rel_root), include_hidden=True):
            dirs[:] = []
            continue

        # Remove ignorable subdirs so we never descend into them
        dirs[:] = [
            d
            for d in dirs
            if not util.is_ignorable(str(rel_root / d), include_hidden=True)
        ]

        src_entries.add(rel_root)
        for f in files:
            if util.is_ignorable(f, include_hidden=True):
                continue
            src_entries.add(rel_root / f)
        for d in dirs:
            if util.is_ignorable(d, include_hidden=True):
                continue
            src_entries.add(rel_root / d)

    for root, dirs, files in os.walk(dst_root, followlinks=False):
        rp = Path(root)
        rel_root = norm_rel(dst_root, rp)

        # If the directory itself is ignorable, skip it entirely
        if util.is_ignorable(str(rel_root), include_hidden=True):
            dirs[:] = []
            continue

        # Remove ignorable subdirs so we never descend into them
        dirs[:] = [
            d
            for d in dirs
            if not util.is_ignorable(str(rel_root / d), include_hidden=True)
        ]

        dst_entries.add(rel_root)
        for f in files:
            dst_entries.add(rel_root / f)
        for d in dirs:
            dst_entries.add(rel_root / d)

    only_in_src = src_entries - dst_entries
    only_in_dst = dst_entries - src_entries

    # Collapse missing dirs to avoid noisy output
    only_in_src = collapse_dirs(only_in_src, src_root)
    only_in_dst = collapse_dirs(only_in_dst, dst_root)

    # Find common entries
    common = src_entries & dst_entries

    for rel in sorted(only_in_src, key=diff_sort_key):
        log.info(f"+ {rel}")
        differences += 1
    for rel in sorted(only_in_dst, key=diff_sort_key):
        log.info(f"- {rel}")
        differences += 1

    # now compare shared paths
    for rel in sorted(common):
        s = src_root / rel
        d = dst_root / rel
        try:
            if s.is_symlink() and d.is_symlink():
                if os.readlink(s) != os.readlink(d):
                    log.info(f"~ {rel} [symlink target differs]")
                    differences += 1
            elif s.is_dir() and not d.is_dir():
                log.warning(f"~ {rel} [dir/file type mismatch]")
                differences += 1
            elif s.is_file() and d.is_file():
                if not same_file(s, d):
                    log.info(f"~ {rel} [changed]")
                    differences += 1
            elif s.is_symlink() != d.is_symlink():
                log.warning(f"~ {rel} [symlink vs non-symlink mismatch]")
                differences += 1
        except Exception as e:
            log.error(f"~ {rel} [error: {e}]")
            differences += 1

    log.debug("diff completed with %d differences", differences)
    return differences


def print_staleness(
    src_epoch: int, dst_epoch: int, threshold: int = config.CACHE_TTL
) -> None:
    """Prints whether the data is stale or fresh based on the given epoch times.

    :param src_epoch: Epoch time (ns) of the source tree.
    :param dst_epoch: Epoch time (ns) of the destination tree.
    :param threshold: Threshold in seconds to consider stale.
    """
    if src_epoch is None:
        print("missing source epoch file")
        return

    age_ns = int(src_epoch) - int(dst_epoch)
    age_sec = age_ns / 1e9

    # print staleness status (with last update time if stale, epoch is in ns)
    if age_sec > threshold:
        stale_time = datetime.fromtimestamp(int(dst_epoch) / 1e9).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        print(f"cache is stale (last update {stale_time})")
    else:
        print("cache is fresh")


def cache(
    src_root: Path = config.DEPLOY_ROOT,
    dst_root: Path = config.CACHE_ROOT,
    workers: int = 16,
    force: bool = False,
) -> None:
    """Clone src_root to dst_root using multiple threads.

    Latest-only behavior:
      - Do NOT traverse any 'versions/' directory by default.
      - Recreate symlinks outside 'versions/'.
      - Copy ONLY the version objects referenced by those symlinks.

    Optimizations:
      - Planning phase shows an indeterminate tqdm (Option C).
      - If a referenced version object already exists in cache, skip copying it.
      - No dynamic mutation of file_ops during execution (avoids missed copies).
    """
    if not src_root.exists():
        raise SystemExit(f"Source does not exist: {src_root}")
    ensure_dir(dst_root)

    dst_symlinks_ok = True
    if is_windows():
        dst_symlinks_ok = can_create_symlinks(dst_root)

    t0 = time.time()

    file_ops: List[Tuple[Path, Path]] = []
    link_ops: List[Tuple[Path, Path]] = []

    # absolute paths under src_root/.../versions/...
    version_objects: Set[Path] = set()

    def _skip_versions_dir(dirs: list) -> None:
        if "versions" in dirs:
            dirs.remove("versions")

    def _link_points_into_versions(src_link: Path) -> Optional[Path]:
        try:
            target_text = os.readlink(src_link)
        except OSError:
            return None
        norm = target_text.replace("\\", "/")
        if not norm.startswith("versions/"):
            return None
        return src_link.parent / target_text

    # indeterminate tqdm: just show activity while we scan.
    with tqdm(
        desc=f"[walking {src_root}]",
        unit="op",
        leave=False,
        position=0,
    ) as plan:
        for root, dirs, files in os.walk(src_root, followlinks=False):
            _skip_versions_dir(dirs)

            if util.is_ignorable(root, include_hidden=True):
                dirs[:] = []
                continue

            root_p = Path(root)
            rel_root = norm_rel(src_root, root_p)
            dst_dir = dst_root / rel_root
            ensure_dir(dst_dir)

            for fname in files:
                plan.update(1)

                if util.is_ignorable(fname, include_hidden=True):
                    continue

                s = root_p / fname
                d = dst_dir / fname

                if s.is_symlink():
                    link_ops.append((s, d))
                    vo = _link_points_into_versions(s)
                    if vo is not None:
                        version_objects.add(vo)
                else:
                    file_ops.append((s, d))

            for dname in list(dirs):
                plan.update(1)

                if util.is_ignorable(dname, include_hidden=True):
                    dirs.remove(dname)
                    continue

                sdir = root_p / dname
                if sdir.is_symlink():
                    dirs.remove(dname)
                    ddst = dst_dir / dname
                    link_ops.append((sdir, ddst))

                    vo = _link_points_into_versions(sdir)
                    if vo is not None:
                        version_objects.add(vo)

        # check/expand referenced version objects
        vos = list(sorted(version_objects))

        missing = 0
        planned_files = 0
        planned_links = 0

        # optional: change description so the user sees a phase shift
        plan.set_description_str(f"[comparing {dst_root}]")

        for vo in vos:
            plan.update(1)  # "checked ref"

            try:
                # local-first shortcut to avoid remote stat in the common case
                rel = vo.relative_to(src_root)
                dst_vo = dst_root / rel

                if not force and dst_vo.exists():
                    continue

                # remote existence check (can be slow over VPN)
                if not vo.exists():
                    continue

                missing += 1
                plan.set_postfix_str(
                    f"refs={len(vos)} missing={missing} files={planned_files} links={planned_links}"
                )

                if vo.is_file():
                    file_ops.append((vo, dst_vo))
                    planned_files += 1
                    continue

                if vo.is_dir():
                    for r2, _, f2 in os.walk(vo, followlinks=False):
                        rp2 = Path(r2)
                        rel2 = rp2.relative_to(vo)
                        out_dir = dst_vo / rel2
                        ensure_dir(out_dir)

                        plan.update(1)  # directory planned

                        for fn in f2:
                            sp = rp2 / fn
                            dp = out_dir / fn
                            if sp.is_symlink():
                                link_ops.append((sp, dp))
                                planned_links += 1
                            else:
                                file_ops.append((sp, dp))
                                planned_files += 1

                            # rate-limit UI updates to keep planning fast
                            if (planned_files + planned_links) % 200 == 0:
                                plan.set_postfix_str(
                                    f"refs={len(vos)} missing={missing} files={planned_files} links={planned_links}"
                                )

            except Exception as e:
                log.warning(f"failed to expand version object {vo}: {e}")

        plan.set_postfix_str(
            f"refs={len(vos)} missing={missing} files={planned_files} links={planned_links}"
        )

    # total ops known: links + file copies
    total_ops = len(link_ops) + len(file_ops)
    copied = skipped = errors = 0

    # execute links first (no dynamic file_ops mutation anymore)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex, tqdm(
        total=total_ops,
        desc=f"[caching {src_root}]",
        unit="op",
        leave=True,
        position=0,
    ) as pbar:
        # 1) create/preserve symlinks
        for s_link, d_link in link_ops:
            try:
                if create_symlink(s_link, d_link, dst_symlinks_ok):
                    pbar.update(1)
                    continue

                # fallback: no symlink support -> dereference and copy content into link path
                target = s_link.parent / os.readlink(s_link)

                if target.exists():
                    if target.is_dir():
                        # best effort: copy directory tree contents into d_link
                        # NOTE: This is rare in your use-case; keep it simple.
                        for r2, _, f2 in os.walk(target, followlinks=False):
                            rp2 = Path(r2)
                            rel2 = rp2.relative_to(target)
                            out_dir = d_link / rel2
                            ensure_dir(out_dir)
                            for fn in f2:
                                file_ops.append((rp2 / fn, out_dir / fn))
                    else:
                        file_ops.append((target, d_link))
                pbar.update(1)
            except Exception as e:
                log.warning(f"{s_link}: {e}")
                pbar.update(1)

        # if we appended fallback file_ops above, adjust total and refresh
        # (still safe because we haven't launched futures yet)
        new_total = len(link_ops) + len(file_ops)
        if new_total != total_ops:
            pbar.total = new_total
            pbar.refresh()
            total_ops = new_total

        # 2) copy files concurrently
        futures = [ex.submit(copy_file_task, s, d) for (s, d) in file_ops]
        for fut in cf.as_completed(futures):
            try:
                res = fut.result()
                if res == "copied":
                    copied += 1
                elif res == "skip":
                    skipped += 1
                elif isinstance(res, str) and res.startswith("error:"):
                    errors += 1
            except Exception:
                errors += 1
            pbar.update(1)

    dt = time.time() - t0
    log.debug(
        "done in %.2fs copied=%d skipped=%d errors=%d", dt, copied, skipped, errors
    )


def _is_dangerous_cache_root(p: Path) -> bool:
    """Best-effort guard against nuking the wrong directory."""
    try:
        rp = p.resolve()
    except Exception:
        rp = p

    # refuse filesystem roots
    if rp == Path(rp.anchor):
        return True

    # refuse very short paths like "/mnt" or "C:\"
    if len(rp.parts) <= 2:
        return True

    return False


def delete_cache(cache_root: Path, dryrun: bool = False) -> int:
    """Delete the entire cache_root directory tree.

    :param cache_root: Root of the cache directory to delete.
    :param dryrun: If True, only print what would be deleted.
    """
    if not cache_root.exists():
        log.error("cache does not exist")
        return 0

    if _is_dangerous_cache_root(cache_root):
        raise SystemExit(f"refusing to delete dangerous cache root: {cache_root}")

    if dryrun:
        log.info(f"would delete cache: {cache_root}")
        return 0

    shutil.rmtree(cache_root, ignore_errors=False)
    log.info(f"deleted cache: {cache_root}")
    return 0


def prune_cache(cache_root: Path, dryrun: bool = False) -> int:
    """Prune unreferenced version objects from cache_root.

    Strategy:
      - Find all 'versions/' directories under cache_root.
      - Determine which version objects are referenced by symlinks *outside* versions/.
      - Remove unreferenced entries directly under each versions/ directory.

    :param cache_root: Root of the cache directory to prune.
    :param dryrun: If True, only print what would be pruned.
    """
    cache_root = cache_root.resolve()
    if not cache_root.exists():
        log.error("cache does not exist")
        return 0

    referenced: Set[Path] = set()

    def _rel(p: Path) -> str:
        """Relative path string for ignorable checks."""
        try:
            return str(p.relative_to(cache_root))
        except Exception:
            return str(p)

    def _is_under_versions(p: Path) -> bool:
        try:
            rel = p.relative_to(cache_root)
        except Exception:
            return False
        return "versions" in rel.parts

    # gather all symlinks outside of any 'versions/' subtree
    for root, dirs, files in os.walk(cache_root, followlinks=False):
        root_p = Path(root)

        if _is_under_versions(root_p):
            dirs[:] = []
            continue

        # ignore ignorable dirs (relative to cache_root)
        if util.is_ignorable(_rel(root_p), include_hidden=True):
            dirs[:] = []
            continue

        # filter ignorable subdirs
        rel_root = Path(_rel(root_p))  # purely for joining relative strings
        kept_dirs = []
        for d in dirs:
            if util.is_ignorable(str(rel_root / d), include_hidden=True):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        # file symlinks
        for fn in files:
            if util.is_ignorable(fn, include_hidden=True):
                continue
            p = root_p / fn
            if not p.is_symlink():
                continue

            try:
                t = os.readlink(p).replace("\\", "/")
            except OSError:
                continue

            if not t.startswith("versions/"):
                continue

            # construct the absolute target path within the cache tree
            target = (p.parent / Path(t)).resolve()
            # ensure it lands under cache_root; otherwise ignore
            try:
                target.relative_to(cache_root)
            except Exception:
                continue
            referenced.add(target)

        # directory symlinks (they show up in dirs list, but we can check via Path)
        for d in list(dirs):
            p = root_p / d
            if not p.is_symlink():
                continue

            try:
                t = os.readlink(p).replace("\\", "/")
            except OSError:
                continue

            if not t.startswith("versions/"):
                continue

            target = (p.parent / Path(t)).resolve()
            try:
                target.relative_to(cache_root)
            except Exception:
                continue
            referenced.add(target)

    # walk every versions dir and delete unreferenced entries directly under it
    removed = 0
    for root, dirs, files in os.walk(cache_root, followlinks=False):
        root_p = Path(root)
        if root_p.name != "versions":
            continue

        # only prune direct children of versions/
        try:
            children = list(root_p.iterdir())
        except Exception:
            continue

        for child in children:
            child_abs = child.resolve()

            # if this exact version-object is referenced, keep it
            if child_abs in referenced:
                continue

            # otherwise, prune
            if dryrun:
                print(f"would prune: {child_abs.relative_to(cache_root)}")
                removed += 1
                continue

            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
            except Exception as e:
                log.warning(f"failed to prune {child_abs}: {e}")

    log.info(f"pruned {removed} unreferenced versions")
    return 0


def build_parser(prog: str = "cache") -> argparse.ArgumentParser:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Cross-platform caching with optional diff.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--src",
        default=config.DEPLOY_ROOT,
        type=Path,
        help="Source directory",
    )
    parser.add_argument(
        "--dst",
        default=config.CACHE_ROOT,
        type=Path,
        help="Destination directory",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(32, (os.cpu_count() or 8) * 4),
        help="Copy threads (default: 4x CPU, capped at 32)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the cache",
    )
    parser.add_argument(
        "-p",
        "--prune",
        action="store_true",
        help="Prune the cache",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show differences only (no copy)",
    )
    parser.add_argument(
        "-t",
        "--ttl",
        type=float,
        default=config.CACHE_TTL,
        help="TTL (seconds) for remote epoch checks (0 = always check)",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force cache even if cache appears fresh (overrides TTL and epoch check)",
    )
    parser.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        help="Dry run (no changes made)",
    )
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse arguments for the cache utility."""
    parser = build_parser()
    return parser.parse_args(list(argv) if argv is not None else None)


def run(args: argparse.Namespace) -> int:
    """Run the cache utility based on parsed arguments."""

    src = args.src.resolve()
    dst = args.dst.resolve()

    if not os.path.exists(src):
        log.error(f"source does not exist: {src}")
        return 1

    # handle delete/prune/diff modes
    if args.delete:
        return delete_cache(dst, dryrun=args.dryrun)
    if args.prune:
        return prune_cache(dst, dryrun=args.dryrun)
    if args.diff:
        diff_trees(src, dst)
        return 0

    # TTL gate (skip remote checks)
    if not args.dryrun and not args.force:
        if not _ttl_expired(dst, args.ttl):
            print("cache check skipped (use -t 0 to override TTL)")
            return 0

    # check epochs to determine staleness
    deploy_epoch = util.read_epoch_file(src)
    cache_epoch = util.read_epoch_file(dst)
    if cache_epoch is None:
        stale = True
    elif deploy_epoch is None:
        stale = True
    elif deploy_epoch == cache_epoch:
        stale = False
    else:
        stale = True

    if args.dryrun:
        print_staleness(deploy_epoch, cache_epoch)
        return STALE_EXIT if stale else 0

    # if fresh, mark checked and exit
    if not stale and not args.force:
        _mark_checked(dst)
        print_staleness(deploy_epoch, cache_epoch)
        return 0

    # perform caching
    try:
        cache(src, dst, workers=args.workers, force=args.force)

    except KeyboardInterrupt:
        log.error("canceled")
        return 1

    except Exception as e:
        log.error(f"cache failed: {e}")
        return 1

    # after cache, sync epoch into cache
    util.write_epoch_file(dst, deploy_epoch)

    # mark checked time
    _mark_checked(dst)

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for cache utility."""

    args = parse_args(argv)

    setup_logging()

    return run(args)
