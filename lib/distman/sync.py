#!/usr/bin/env python3

__doc__ = """
Contains cross-platform directory mirroring utilities.
"""

import argparse
import concurrent.futures as cf
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from tqdm import tqdm
from typing import Tuple, Set

from distman.logger import log, setup_logging

setup_logging()


def is_windows() -> bool:
    """Check if the current operating system is Windows."""
    return os.name == "nt"


def norm_rel(root: Path, p: Path) -> Path:
    """Normalize path p relative to root.

    :param root: Root directory
    :param p: Path to normalize
    :return: Relative path from root to p
    """
    return p.relative_to(root)


def file_signature(p: Path) -> Tuple[int, int]:
    """Get file size and modification time in nanoseconds.

    :param p: Path to the file
    :return: Tuple of (size in bytes, modification time in nanoseconds)
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

    :param src_tmp: Temporary source file path
    :param dst: Destination file path
    """
    os.replace(src_tmp, dst)


def can_create_symlinks(test_dir: Path) -> bool:
    """Check if symlinks can be created in the given directory.

    :param test_dir: Directory to test symlink creation
    :return: True if symlinks can be created, False otherwise
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

    :param src_link: Source symlink path
    :param dst_link: Destination symlink path
    :param dst_supports_symlinks: Whether the destination supports symlinks
    :return: True if symlink was created, False otherwise
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

    :param src: Source file path
    :param dst: Destination file path
    :return: True if files are the same, False otherwise
    """
    try:
        s1, m1 = file_signature(src)
        s2, m2 = file_signature(dst)
        return s1 == s2 and m1 == m2
    except FileNotFoundError:
        return False


def copy_file_task(src: Path, dst: Path) -> str:
    """Copy a single file from src to dst atomically.

    :param src: Source file path
    :param dst: Destination file path
    :return: "copied", "skip", or "error:<message>"
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


def copy_tree_fallback(
    src_dir: Path, dst_dir: Path, executor: cf.Executor, results: list
) -> None:
    """Fallback copy tree that does not handle symlinks.

    :param src_dir: Source directory
    :param dst_dir: Destination directory
    :param executor: Executor to submit copy tasks to
    """
    for root, dirs, files in os.walk(src_dir, followlinks=False):
        root_p = Path(root)
        rel = root_p.relative_to(src_dir)
        ensure_dir(dst_dir / rel)
        for f in files:
            s = root_p / f
            d = dst_dir / rel / f
            results.append(executor.submit(copy_file_task, s, d))


def diff_trees(src_root: Path, dst_root: Path) -> int:
    """Compare src and dst recursively. Returns number of differences.

    :param src_root: Source directory
    :param dst_root: Destination directory
    :return: Number of differences found
    """
    differences = 0
    log.debug(f"comparing {src_root} -> {dst_root}")

    src_entries: Set[Path] = set()
    dst_entries: Set[Path] = set()

    for root, dirs, files in os.walk(src_root, followlinks=False):
        rp = Path(root)
        rel_root = norm_rel(src_root, rp)
        src_entries.add(rel_root)
        for f in files:
            src_entries.add(rel_root / f)
        for d in dirs:
            src_entries.add(rel_root / d)

    for root, dirs, files in os.walk(dst_root, followlinks=False):
        rp = Path(root)
        rel_root = norm_rel(dst_root, rp)
        dst_entries.add(rel_root)
        for f in files:
            dst_entries.add(rel_root / f)
        for d in dirs:
            dst_entries.add(rel_root / d)

    only_in_src = src_entries - dst_entries
    only_in_dst = dst_entries - src_entries
    common = src_entries & dst_entries

    for rel in sorted(only_in_src):
        log.info(f"+ {rel}")
        differences += 1
    for rel in sorted(only_in_dst):
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


def mirror(
    src_root: Path, dst_root: Path, workers: int = 16, do_delete: bool = False
) -> None:
    """Mirror src_root to dst_root using multiple threads.

    :param src_root: Source directory
    :param dst_root: Destination directory
    :param workers: Number of worker threads
    :param do_delete: Whether to delete files in dst not present in src
    """
    if not src_root.exists():
        raise SystemExit(f"Source does not exist: {src_root}")
    ensure_dir(dst_root)
    dst_symlinks_ok = True
    if is_windows():
        dst_symlinks_ok = can_create_symlinks(dst_root)

    file_tasks = []
    t0 = time.time()

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        total_files = sum(
            len(files) for _, _, files in os.walk(src_root, followlinks=False)
        )
        file_tasks = []

        with tqdm(total=total_files, desc="[mirror]") as pbar:
            for root, dirs, files in os.walk(src_root, followlinks=False):
                root_p = Path(root)
                rel_root = norm_rel(src_root, root_p)
                dst_dir = dst_root / rel_root
                ensure_dir(dst_dir)

                for fname in files:
                    s = root_p / fname
                    d = dst_dir / fname
                    try:
                        if s.is_symlink():
                            if create_symlink(s, d, dst_symlinks_ok):
                                pbar.update(1)
                                continue
                            else:
                                target = s.resolve()
                                file_tasks.append(ex.submit(copy_file_task, target, d))
                        else:
                            file_tasks.append(ex.submit(copy_file_task, s, d))
                    except Exception as e:
                        log.warning(f"{s}: {e}")

                for dname in list(dirs):
                    sdir = root_p / dname
                    if sdir.is_symlink():
                        rel = norm_rel(src_root, sdir)
                        ddst = dst_root / rel
                        try:
                            if create_symlink(sdir, ddst, dst_symlinks_ok):
                                dirs.remove(dname)
                                continue
                        except Exception:
                            dirs.remove(dname)
                            copy_tree_fallback(sdir.resolve(), ddst, ex, file_tasks)

        copied = skipped = errors = 0
        for fut in cf.as_completed(file_tasks):
            res = fut.result()
            if res == "copied":
                copied += 1
            elif res == "skip":
                skipped += 1
            elif isinstance(res, str) and res.startswith("error:"):
                errors += 1
            pbar.update(1)

    deleted = 0
    if do_delete:
        for root, dirs, files in os.walk(dst_root, topdown=False):
            root_p = Path(root)
            rel_root = norm_rel(dst_root, root_p)
            for name in files:
                rel = rel_root / name
                if not (src_root / rel).exists():
                    try:
                        (root_p / name).unlink(missing_ok=True)
                        deleted += 1
                    except Exception:
                        pass
            for name in dirs:
                rel = rel_root / name
                p = root_p / name
                if not (src_root / rel).exists():
                    try:
                        if p.is_symlink():
                            p.unlink(missing_ok=True)
                        else:
                            shutil.rmtree(p, ignore_errors=True)
                        deleted += 1
                    except Exception:
                        pass

    dt = time.time() - t0
    log.debug(
        "done in %.2fs copied=%d skipped=%d deleted=%d errors=%d",
        dt,
        copied,
        skipped,
        deleted,
        errors,
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    ap = argparse.ArgumentParser(
        description="Cross-platform mirror with optional diff."
    )
    ap.add_argument(
        "--src", required=True, type=Path, help="Source directory (/mnt/tools)"
    )
    ap.add_argument("--dst", required=True, type=Path, help="Destination CACHE_ROOT")
    ap.add_argument(
        "--workers",
        type=int,
        default=min(32, (os.cpu_count() or 8) * 4),
        help="Copy threads (default: 4x CPU, capped at 32)",
    )
    ap.add_argument("--delete", action="store_true", help="Delete items not in source")
    ap.add_argument(
        "--diff", action="store_true", help="Show differences only (no copy)"
    )
    return ap.parse_args()


def main() -> None:
    """Main entry point for the dsync utility."""
    args = parse_args()
    src = args.src.resolve()
    dst = args.dst.resolve()
    if args.diff:
        diff_trees(src, dst)
        return
    try:
        mirror(src, dst, workers=args.workers, do_delete=args.delete)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
