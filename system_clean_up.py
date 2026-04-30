#!/usr/bin/env python3
"""MacOS System Cleanup — lean, efficient, conservative macOS cleaner."""

import os
import sys
import subprocess
import argparse
import platform
import time
import shutil
import heapq
import json
from pathlib import Path

import psutil

# ── Constants ────────────────────────────────────────────────────────────────

HOME = str(Path.home())
MB = 1024 * 1024
GB = 1024 ** 3

CONFIG_PATH = os.path.join(HOME, ".system_cleanup_config.json")
LOG_DIR = os.path.join(HOME, "Library/Logs/SystemCleanup")
LOG_PATH = os.path.join(LOG_DIR, "cleanup.log")

ALLOWED_ROOTS_RAW = [HOME, "/tmp", "/private/var/folders"]

SKIP_DIRS = frozenset({".Trash", "Keychains", "Preferences", "Extensions"})
SKIP_FILES = frozenset({"com.apple.dock.plist", "com.apple.finder.plist"})

# Directories that are purely cache — safe to wipe contents
CACHE_DIRS = [
    os.path.join(HOME, "Library/Caches"),
    os.path.join(HOME, "Library/Logs"),
    os.path.join(HOME, "Library/Application Support/Google/Chrome/Default/Cache"),
    os.path.join(HOME, "Library/Application Support/Google/Chrome/Default/Code Cache"),
    os.path.join(HOME, "Library/Application Support/Firefox/Profiles"),
    os.path.join(HOME, "Library/Safari/Cache"),
    os.path.join(HOME, "Library/Containers/com.apple.Safari/Data/Library/Caches"),
    os.path.join(HOME, "Library/Containers/com.apple.mail/Data/Library/Caches"),
    os.path.join(HOME, "Library/Containers/com.apple.Photos/Data/Library/Caches"),
    "/tmp",
    "/private/var/folders",
]

# Developer caches — large, fully regenerated on next build/install
DEV_CACHE_DIRS = [
    os.path.join(HOME, "Library/Developer/Xcode/DerivedData"),
    os.path.join(HOME, "Library/Caches/Homebrew"),
    os.path.join(HOME, ".npm/_cacache"),
    os.path.join(HOME, "Library/Caches/pip"),
    os.path.join(HOME, "Library/Caches/Yarn"),
    os.path.join(HOME, "Library/Caches/CocoaPods"),
    os.path.join(HOME, ".composer/cache"),
    os.path.join(HOME, ".gradle/caches"),
]

LOG_CLEANUP_DIRS = [
    os.path.join(HOME, "Library/Logs/DiagnosticReports"),
    os.path.join(HOME, "Library/Logs/CrashReporter"),
]

MAIL_DOWNLOADS = os.path.join(
    HOME, "Library/Containers/com.apple.mail/Data/Library/Mail Downloads"
)

# ── ANSI Colors ──────────────────────────────────────────────────────────────

class C:
    """ANSI color codes — disabled when not a TTY."""
    _on = sys.stdout.isatty()
    BOLD = "\033[1m" if _on else ""
    DIM = "\033[2m" if _on else ""
    GREEN = "\033[32m" if _on else ""
    YELLOW = "\033[33m" if _on else ""
    RED = "\033[31m" if _on else ""
    CYAN = "\033[36m" if _on else ""
    RESET = "\033[0m" if _on else ""
    OK = "\033[32m✓\033[0m" if _on else "[OK]"
    SKIP = "\033[2m⊘\033[0m" if _on else "[-]"
    DOT = "\033[36m●\033[0m" if _on else "*"


# ── SystemCleanup ────────────────────────────────────────────────────────────

class SystemCleanup:
    def __init__(self, dry_run: bool = False):
        if platform.system() != "Darwin":
            print(f"{C.RED}Error: This script only runs on macOS.{C.RESET}")
            sys.exit(1)

        self.dry_run = dry_run
        self.allowed_roots = [os.path.realpath(r) for r in ALLOWED_ROOTS_RAW]
        self.stats = {"total_size": 0, "total_files": 0, "last_run": None}
        self._load_config()
        self._ensure_log_dir()
        self._log("Session started" + (" [DRY RUN]" if dry_run else ""))

    # ── Config ───────────────────────────────────────────────────────────

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    self.stats = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

    def _save_config(self):
        self.stats["last_run"] = time.time()
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.stats, f)
        except IOError:
            pass

    # ── Logging ──────────────────────────────────────────────────────────

    def _ensure_log_dir(self):
        os.makedirs(LOG_DIR, exist_ok=True)

    def _log(self, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(LOG_PATH, "a") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    # ── Safety ───────────────────────────────────────────────────────────

    def _is_safe(self, path: str) -> bool:
        """Check path is within allowed roots and is not a root itself."""
        try:
            real = os.path.realpath(path)
            # Never delete an allowed root directory itself
            if real in self.allowed_roots:
                return False
            return any(real.startswith(root + os.sep) for root in self.allowed_roots)
        except (PermissionError, OSError):
            return False

    # ── Deletion ─────────────────────────────────────────────────────────

    def _safe_delete(self, path: str, size_mb: float = 0) -> bool:
        """Unified safe deletion for files and directories."""
        if os.path.islink(path):
            return False
        if not self._is_safe(path):
            self._log(f"Blocked: {path}")
            return False

        label = f"{size_mb:.2f} MB" if size_mb > 0.01 else "<0.01 MB"

        if self.dry_run:
            self._log(f"[DRY] {path} ({label})")
            return True

        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            self._log(f"Deleted: {path} ({label})")
            return True
        except (PermissionError, OSError) as e:
            self._log(f"Failed: {path}: {e}")
            return False

    # ── Scanning ─────────────────────────────────────────────────────────

    def _scan_files(self, directory: str, min_age_s: float = 0):
        """Fast recursive scanner using os.scandir. Yields (path, size_mb)."""
        if not os.path.isdir(directory):
            return

        now = time.time()
        stack = [directory]

        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        name = entry.name

                        # Skip protected dirs
                        if entry.is_dir(follow_symlinks=False):
                            if name in SKIP_DIRS or name.startswith("."):
                                continue
                            stack.append(entry.path)
                            continue

                        # Skip symlinks, hidden files, system files
                        if entry.is_symlink():
                            continue
                        if name.startswith(".") or name in SKIP_FILES:
                            continue

                        try:
                            st = entry.stat(follow_symlinks=False)
                            if min_age_s > 0 and (now - st.st_mtime) < min_age_s:
                                continue
                            yield entry.path, st.st_size / MB
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue

    # ── Cleanup Operations ───────────────────────────────────────────────

    def clean_caches(self, days: int = 7, verbose: bool = False) -> tuple:
        """Clean system and application caches (merged — no double-scanning)."""
        age_s = days * 86400
        total_files = 0
        total_mb = 0.0

        for d in CACHE_DIRS:
            if not os.path.isdir(d):
                continue
            if verbose:
                print(f"  {C.DIM}Scanning {d}{C.RESET}")

            for path, size in self._scan_files(d, min_age_s=age_s):
                if self._safe_delete(path, size):
                    total_files += 1
                    total_mb += size

        return total_files, total_mb

    def clean_dev_caches(self, verbose: bool = False) -> tuple:
        """Clean developer tool caches (Xcode DerivedData, Homebrew, npm, pip, etc.)."""
        total_files = 0
        total_mb = 0.0

        for d in DEV_CACHE_DIRS:
            if not os.path.isdir(d):
                continue
            if verbose:
                print(f"  {C.DIM}Cleaning {d}{C.RESET}")

            for path, size in self._scan_files(d):
                if self._safe_delete(path, size):
                    total_files += 1
                    total_mb += size

        return total_files, total_mb

    def clean_ds_store(self, verbose: bool = False) -> tuple:
        """Remove .DS_Store files from home directory."""
        total_files = 0
        total_mb = 0.0

        stack = [HOME]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in SKIP_DIRS:
                                stack.append(entry.path)
                            continue
                        if entry.name == ".DS_Store" and not entry.is_symlink():
                            try:
                                size = entry.stat(follow_symlinks=False).st_size / MB
                                if self._safe_delete(entry.path, size):
                                    total_files += 1
                                    total_mb += size
                            except (PermissionError, OSError):
                                continue
            except (PermissionError, OSError):
                continue

        return total_files, total_mb

    def clean_logs(self, verbose: bool = False) -> tuple:
        """Clean old diagnostic reports and crash logs."""
        total_files = 0
        total_mb = 0.0

        for d in LOG_CLEANUP_DIRS:
            if not os.path.isdir(d):
                continue
            if verbose:
                print(f"  {C.DIM}Cleaning {d}{C.RESET}")

            for path, size in self._scan_files(d):
                if self._safe_delete(path, size):
                    total_files += 1
                    total_mb += size

        return total_files, total_mb

    def clean_mail(self, verbose: bool = False) -> tuple:
        """Clean Mail.app downloaded attachments (copies — originals stay in Mail)."""
        total_files = 0
        total_mb = 0.0

        if not os.path.isdir(MAIL_DOWNLOADS):
            return total_files, total_mb

        if verbose:
            print(f"  {C.DIM}Cleaning {MAIL_DOWNLOADS}{C.RESET}")

        for path, size in self._scan_files(MAIL_DOWNLOADS):
            if self._safe_delete(path, size):
                total_files += 1
                total_mb += size

        return total_files, total_mb

    def clean_language_files(self, verbose: bool = False) -> tuple:
        """Remove non-English .lproj bundles from applications."""
        keep = frozenset({"en.lproj", "en_US.lproj", "English.lproj", "Base.lproj"})
        search_roots = [
            os.path.join(HOME, "Library/Application Support"),
            "/Applications",
        ]
        total_files = 0
        total_mb = 0.0

        for root_dir in search_roots:
            if not os.path.isdir(root_dir):
                continue
            if verbose:
                print(f"  {C.DIM}Scanning {root_dir}{C.RESET}")

            stack = [root_dir]
            while stack:
                current = stack.pop()
                try:
                    with os.scandir(current) as it:
                        for entry in it:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                            if entry.name.endswith(".lproj") and entry.name not in keep:
                                # Calculate size then delete the whole bundle
                                size = 0.0
                                for fp, fs in self._scan_files(entry.path):
                                    size += fs
                                if self._safe_delete(entry.path, size):
                                    total_files += 1
                                    total_mb += size
                            else:
                                stack.append(entry.path)
                except (PermissionError, OSError):
                    continue

        return total_files, total_mb

    def empty_trash(self, verbose: bool = False) -> tuple:
        """Empty the user's Trash."""
        trash = os.path.join(HOME, ".Trash")
        total_files = 0
        total_mb = 0.0

        if not os.path.isdir(trash):
            return total_files, total_mb

        if verbose:
            print(f"  {C.DIM}Emptying {trash}{C.RESET}")

        try:
            with os.scandir(trash) as it:
                for entry in it:
                    if entry.is_symlink():
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            size = sum(s for _, s in self._scan_files(entry.path))
                        else:
                            size = entry.stat(follow_symlinks=False).st_size / MB
                        if self._safe_delete(entry.path, size):
                            total_files += 1
                            total_mb += size
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            pass

        return total_files, total_mb

    def flush_dns(self, verbose: bool = False) -> bool:
        """Actually flush the DNS cache (prompts for admin password via GUI dialog)."""
        if verbose:
            print(f"  {C.DIM}Flushing DNS cache...{C.RESET}")

        try:
            subprocess.run(
                ["dscacheutil", "-flushcache"],
                check=True,
                capture_output=True,
            )
        except subprocess.SubprocessError:
            pass

        # Use osascript for the sudo commands — shows a native password dialog
        script = 'do shell script "killall -HUP mDNSResponder 2>/dev/null; true" with administrator privileges'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
            )
            self._log("DNS cache flushed")
            return True
        except subprocess.SubprocessError:
            self._log("DNS flush: user cancelled or failed")
            return False

    def free_ram(self, verbose: bool = False) -> bool:
        """Actually purge inactive memory (prompts for admin password via GUI dialog)."""
        if verbose:
            print(f"  {C.DIM}Purging inactive memory...{C.RESET}")

        script = 'do shell script "purge" with administrator privileges'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
            )
            self._log("Memory purged")
            return True
        except subprocess.SubprocessError:
            self._log("Memory purge: user cancelled or failed")
            return False

    def reset_quicklook(self, verbose: bool = False) -> bool:
        """Reset the QuickLook thumbnail cache."""
        if verbose:
            print(f"  {C.DIM}Resetting QuickLook cache...{C.RESET}")

        try:
            subprocess.run(
                ["qlmanage", "-r", "cache"],
                check=True,
                capture_output=True,
            )
            self._log("QuickLook cache reset")
            return True
        except subprocess.SubprocessError:
            self._log("QuickLook reset failed")
            return False

    def find_large_files(
        self, min_size_mb: float = 100, limit: int = 50
    ) -> list:
        """Find the largest files in the home directory using a min-heap."""
        heap = []

        stack = [HOME]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                                stack.append(entry.path)
                            continue
                        if entry.is_symlink():
                            continue
                        try:
                            size = entry.stat(follow_symlinks=False).st_size / MB
                            if size >= min_size_mb:
                                if len(heap) < limit:
                                    heapq.heappush(heap, (size, entry.path))
                                elif size > heap[0][0]:
                                    heapq.heapreplace(heap, (size, entry.path))
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue

        return [(p, s) for s, p in sorted(heap, reverse=True)]

    def system_info(self) -> dict:
        """Get current system resource usage."""
        disk = shutil.disk_usage("/")
        mem = psutil.virtual_memory()
        return {
            "disk_total_gb": disk.total / GB,
            "disk_used_gb": disk.used / GB,
            "disk_free_gb": disk.free / GB,
            "disk_pct": disk.used / disk.total * 100,
            "mem_total_gb": mem.total / GB,
            "mem_used_gb": mem.used / GB,
            "mem_free_gb": mem.available / GB,
            "mem_pct": mem.percent,
            "cpu_pct": psutil.cpu_percent(interval=0.3),
            "last_run": self.stats.get("last_run"),
            "total_cleaned_mb": self.stats.get("total_size", 0),
            "total_cleaned_files": self.stats.get("total_files", 0),
        }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _bar(pct: float, w: int = 20) -> str:
    f = int(pct / 100 * w)
    color = C.GREEN if pct < 60 else C.YELLOW if pct < 85 else C.RED
    return f"{color}{'█' * f}{C.DIM}{'░' * (w - f)}{C.RESET}"


def _sz(mb: float) -> str:
    if mb < 0.01:
        return "—"
    return f"{mb:.1f} MB" if mb < 1024 else f"{mb / 1024:.2f} GB"


def main():
    parser = argparse.ArgumentParser(description="Lean macOS system cleaner")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    parser.add_argument("--days", type=int, default=7, help="Cache age threshold in days (default: 7)")
    parser.add_argument("--aggressive", action="store_true", help="Include language files cleanup")
    parser.add_argument("--skip", nargs="+", default=[], metavar="OP",
                        help="Skip: caches dev ds_store logs mail trash quicklook dns ram language")
    parser.add_argument("--analyze", action="store_true", help="Find large files only")
    parser.add_argument("--info", action="store_true", help="Show system info only")
    parser.add_argument("-v", "--verbose", action="store_true", help="Detailed progress")
    args = parser.parse_args()

    skip = set(args.skip)
    cleaner = SystemCleanup(dry_run=args.dry_run)

    # ── Header & system info ─────────────────────────────────────────────
    mode = f"{C.YELLOW}DRY RUN{C.RESET}" if args.dry_run else f"{C.GREEN}LIVE{C.RESET}"
    print(f"\n  {C.BOLD}Mac Clean{C.RESET}  {C.DIM}·{C.RESET}  {mode}\n")

    info = cleaner.system_info()
    print(f"  {C.BOLD}DISK{C.RESET}    {_bar(info['disk_pct'])}  "
          f"{info['disk_used_gb']:.1f}/{info['disk_total_gb']:.1f} GB  "
          f"{C.GREEN}{info['disk_free_gb']:.1f} GB free{C.RESET}")
    print(f"  {C.BOLD}MEMORY{C.RESET}  {_bar(info['mem_pct'])}  "
          f"{info['mem_used_gb']:.1f}/{info['mem_total_gb']:.1f} GB  "
          f"{C.GREEN}{info['mem_free_gb']:.1f} GB free{C.RESET}")
    print(f"  {C.BOLD}CPU{C.RESET}     {_bar(info['cpu_pct'], 10)}  {info['cpu_pct']:.0f}%")
    if info["last_run"]:
        t = time.strftime("%b %d %H:%M", time.localtime(info["last_run"]))
        print(f"  {C.DIM}Last: {t} · {info['total_cleaned_files']} files · {_sz(info['total_cleaned_mb'])}{C.RESET}")
    print()

    if args.info:
        return

    # ── Analyze mode ─────────────────────────────────────────────────────
    if args.analyze:
        print(f"  {C.BOLD}Large files (≥ 100 MB){C.RESET}\n")
        large = cleaner.find_large_files(min_size_mb=100, limit=20)
        if not large:
            print(f"  {C.DIM}None found.{C.RESET}\n")
            return
        for i, (path, size) in enumerate(large, 1):
            s = f"{size:.0f} MB" if size < 1024 else f"{size / 1024:.1f} GB"
            sc = C.RED if size > 1024 else C.YELLOW if size > 500 else ""
            print(f"  {i:>3}  {sc}{s:>10}{C.RESET}  {C.DIM}{path}{C.RESET}")
        print()
        return

    # ── Operations table ─────────────────────────────────────────────────
    # (key, label, method_name, method_kwargs, aggressive_only)
    ops = [
        ("caches",    "System & app caches",       "clean_caches",        {"days": args.days}, False),
        ("dev",       "Developer caches",          "clean_dev_caches",    {},                  False),
        ("ds_store",  ".DS_Store files",           "clean_ds_store",      {},                  False),
        ("logs",      "Crash reports",             "clean_logs",          {},                  False),
        ("mail",      "Mail downloads",            "clean_mail",          {},                  False),
        ("trash",     "Trash",                     "empty_trash",         {},                  False),
        ("quicklook", "QuickLook cache",           "reset_quicklook",     {},                  False),
        ("dns",       "DNS cache",                 "flush_dns",           {},                  False),
        ("ram",       "Inactive memory",           "free_ram",            {},                  False),
        ("language",  "Language files",            "clean_language_files", {},                  True),
    ]

    results = []
    for key, label, method, kwargs, aggressive_only in ops:
        if key in skip:
            continue
        if aggressive_only and not args.aggressive:
            continue

        print(f"  {C.DOT} {label}")
        ret = getattr(cleaner, method)(verbose=args.verbose, **kwargs)

        # Methods return (files, mb) or bool
        if isinstance(ret, tuple):
            results.append((label, ret[0], ret[1]))
        else:
            results.append((label, 1 if ret else 0, 0.0))

    # ── Results ──────────────────────────────────────────────────────────
    W = 58
    print(f"\n  {C.DIM}{'─' * W}{C.RESET}")
    print(f"  {C.BOLD}{'':>2}{'Operation':<30} {'Files':>7}  {'Freed':>12}{C.RESET}")
    print(f"  {C.DIM}{'─' * W}{C.RESET}")

    total_f, total_mb = 0, 0.0
    for name, files, mb in results:
        icon = C.OK if files > 0 else C.SKIP
        color = "" if files > 0 else C.DIM
        print(f"  {icon} {color}{name:<30} {files:>7}  {_sz(mb):>12}{C.RESET}")
        total_f += files
        total_mb += mb

    print(f"  {C.DIM}{'─' * W}{C.RESET}")
    print(f"  {C.BOLD}{C.GREEN}  {'TOTAL':<30} {total_f:>7}  {_sz(total_mb):>12}{C.RESET}")
    print(f"  {C.DIM}{'─' * W}{C.RESET}")

    cleaner.stats.update({"total_files": total_f, "total_size": total_mb})
    cleaner._save_config()

    if args.dry_run:
        print(f"\n  {C.YELLOW}Dry run — nothing was deleted.{C.RESET}")
    print()


if __name__ == "__main__":
    main()