#!/usr/bin/env python3

__doc__ = """
Contains cross-platform directory cloning utilities.
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
from typing import Optional, Tuple, Set

from distman import config, util
from distman.logger import log, setup_logging

setup_logging()


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

    # Identify which missing paths are dirs in the tree we are reporting from.
    paths = set()
    for rel in dirs:
        try:
            if (root / rel).is_dir():
                paths.add(rel)
        except Exception:
            # If we can't stat it, don't treat as dir for collapsing.
            pass

    if not paths:
        return dirs

    # Keep rel only if no missing dir is a parent of rel (excluding itself).
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


def clone(
    src_root: Path = config.DEPLOY_ROOT,
    dst_root: Path = config.CACHE_ROOT,
    workers: int = 16,
    do_delete: bool = False,
) -> None:
    """Clone src_root to dst_root using multiple threads.

    Latest-only behavior:
      - Do NOT traverse any 'versions/' directory by default.
      - Recreate symlinks outside 'versions/'.
      - Copy ONLY the version objects referenced by those symlinks.

    Progress behavior:
      - Plan all file copy ops first (accurate totals).
      - tqdm total == (#symlink ops + #file copy ops).
    """
    if not src_root.exists():
        raise SystemExit(f"Source does not exist: {src_root}")
    ensure_dir(dst_root)

    dst_symlinks_ok = True
    if is_windows():
        dst_symlinks_ok = can_create_symlinks(dst_root)

    t0 = time.time()

    # ------------------------------------------------------------------
    # Planning phase
    # ------------------------------------------------------------------
    # file_ops: list[(src_file, dst_file)]
    file_ops = []
    # link_ops: list[(src_link, dst_link)]
    link_ops = []
    # version_objects: set[Path]  (absolute paths under src_root/.../versions/...)
    version_objects: Set[Path] = set()

    def _skip_versions_dir(dirs: list) -> None:
        # Prevent walking into ANY directory literally named 'versions'
        # at any depth.
        if "versions" in dirs:
            dirs.remove("versions")

    def _link_points_into_versions(src_link: Path) -> Optional[Path]:
        """If src_link points to a versions object, return absolute path to that object, else None."""
        try:
            target_text = os.readlink(src_link)
        except OSError:
            return None

        # distman convention is a relative link like "versions/name.N.hash"
        # Normalize slashes for simple checks.
        norm = target_text.replace("\\", "/")
        if not norm.startswith("versions/"):
            return None

        # Keep it cheap: avoid resolve() unless needed.
        candidate = src_link.parent / target_text
        return candidate

    # Walk src_root excluding versions/
    for root, dirs, files in os.walk(src_root, followlinks=False):
        _skip_versions_dir(dirs)

        # util.is_ignorable() expects path-ish; keep your existing behavior
        if util.is_ignorable(root, include_hidden=True):
            # Don't descend into ignored dirs
            dirs[:] = []
            continue

        root_p = Path(root)
        rel_root = norm_rel(src_root, root_p)
        dst_dir = dst_root / rel_root
        ensure_dir(dst_dir)

        # Handle files at this level
        for fname in files:
            if util.is_ignorable(fname, include_hidden=True):
                continue

            s = root_p / fname
            d = dst_dir / fname

            if s.is_symlink():
                link_ops.append((s, d))

                vo = _link_points_into_versions(s)
                if vo is not None:
                    # Track the referenced version object (file or dir)
                    version_objects.add(vo)
                else:
                    # If it points elsewhere, we still try to preserve the link
                    # (no extra payload copying planned).
                    pass
            else:
                file_ops.append((s, d))

        # Handle directory entries (symlink dirs appear here)
        for dname in list(dirs):
            if util.is_ignorable(dname, include_hidden=True):
                dirs.remove(dname)
                continue

            sdir = root_p / dname
            if sdir.is_symlink():
                # Don't descend into symlinked dirs
                dirs.remove(dname)

                ddst = dst_dir / dname
                link_ops.append((sdir, ddst))

                vo = _link_points_into_versions(sdir)
                if vo is not None:
                    version_objects.add(vo)
                else:
                    # Link to non-versions path; preserve link if possible
                    pass

    # Expand version_objects -> individual file copy ops
    # Note: version object can be a file or directory.
    expanded_version_count = 0
    for vo in sorted(version_objects):
        try:
            # Normalize / resolve only enough to determine file vs dir.
            # Avoid .resolve() here; it can be expensive on high latency FS.
            if not vo.exists():
                # If link target is broken, skip it but keep the link op.
                log.warning(f"version object does not exist: {vo}")
                continue

            rel = vo.relative_to(src_root)
            dst_vo = dst_root / rel

            if vo.is_file():
                file_ops.append((vo, dst_vo))
                expanded_version_count += 1
            elif vo.is_dir():
                for r2, d2, f2 in os.walk(vo, followlinks=False):
                    rp2 = Path(r2)
                    rel2 = rp2.relative_to(vo)
                    out_dir = dst_vo / rel2
                    ensure_dir(out_dir)

                    for fn in f2:
                        sp = rp2 / fn
                        dp = out_dir / fn
                        # We generally do not expect symlinks inside version payloads,
                        # but handle them safely.
                        if sp.is_symlink():
                            # Best-effort: preserve if possible; otherwise copy target contents.
                            link_ops.append((sp, dp))
                            tgt = _link_points_into_versions(sp)
                            if tgt is not None:
                                version_objects.add(tgt)
                            else:
                                # If symlinks unsupported, we will dereference later in execution.
                                pass
                        else:
                            file_ops.append((sp, dp))
                            expanded_version_count += 1
            else:
                log.warning(f"version object is neither file nor dir: {vo}")
        except Exception as e:
            log.warning(f"failed to expand version object {vo}: {e}")

    # Progress total: count link creations + file copies.
    # (We update once per link op, and once per completed copy task.)
    total_ops = len(link_ops) + len(file_ops)

    # ------------------------------------------------------------------
    # Execution phase
    # ------------------------------------------------------------------
    copied = skipped = errors = 0

    with cf.ThreadPoolExecutor(max_workers=workers) as ex, tqdm(
        total=total_ops, desc="[clone]", unit="op"
    ) as pbar:
        # 1) Create / preserve symlinks (fast ops; update progress immediately)
        for s_link, d_link in link_ops:
            try:
                if create_symlink(s_link, d_link, dst_symlinks_ok):
                    pbar.update(1)
                    continue

                # Fallback: dst doesn't support symlinks.
                # Dereference and copy contents into the link path.
                target = s_link.parent / os.readlink(s_link)
                if target.exists():
                    if target.is_dir():
                        # Copy directory contents into d_link
                        ensure_dir(d_link)
                        for r2, _, f2 in os.walk(target, followlinks=False):
                            rp2 = Path(r2)
                            rel2 = rp2.relative_to(target)
                            out_dir = d_link / rel2
                            ensure_dir(out_dir)
                            for fn in f2:
                                file_ops.append((rp2 / fn, out_dir / fn))
                        # We added more file ops; adjust progress total
                        pbar.total += sum(
                            1 for _ in os.walk(target, followlinks=False) for __ in _[2]
                        )
                        pbar.refresh()
                    else:
                        file_ops.append((target, d_link))
                        pbar.total += 1
                        pbar.refresh()
                pbar.update(1)
            except Exception as e:
                log.warning(f"{s_link}: {e}")
                pbar.update(1)

        # 2) Copy files concurrently (accurate progress = completions)
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

    # ------------------------------------------------------------------
    # Optional delete (NOTE: this does NOT prune old versions; it mirrors existence only)
    # ------------------------------------------------------------------
    deleted = 0
    if do_delete:
        # With "latest-only" cloning, you probably want a separate `prune` command
        # to remove unreferenced cached versions. This delete mirrors src existence.
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
    parser = argparse.ArgumentParser(
        description="Cross-platform clone with optional diff."
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
    ),
    parser.add_argument(
        "--workers",
        type=int,
        default=min(32, (os.cpu_count() or 8) * 4),
        help="Copy threads (default: 4x CPU, capped at 32)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete items not in source",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Show differences only (no copy)",
    )
    return parser.parse_args()


def main():
    """Main entry point for the dsync utility."""
    args = parse_args()
    src = args.src.resolve()
    dst = args.dst.resolve()
    if args.diff:
        diff_trees(src, dst)
        return
    try:
        clone(src, dst, workers=args.workers, do_delete=args.delete)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
