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
import stat
import atexit
import locale
from pathlib import Path

import psutil

# ── Constants ────────────────────────────────────────────────────────────────

HOME = str(Path.home())
MB = 1024 * 1024
GB = 1024 ** 3
DAY = 86400
DEFAULT_DAYS = 7
MIN_LOG_DAYS = 30
MIN_MAIL_DAYS = 30
LOG_MAX_BYTES = 5 * MB

CONFIG_PATH = os.path.join(HOME, ".system_cleanup_config.json")
LOG_DIR = os.path.join(HOME, "Library/Logs/SystemCleanup")
LOG_PATH = os.path.join(LOG_DIR, "cleanup.log")

ALLOWED_ROOTS_RAW = [HOME, "/tmp", "/private/var/folders"]

SKIP_DIRS = frozenset({".Trash", "Keychains", "Preferences", "Extensions"})
SKIP_FILES = frozenset({"com.apple.dock.plist", "com.apple.finder.plist"})

# Directories that are purely cache — safe to wipe contents
CACHE_DIRS = [
    os.path.join(HOME, "Library/Caches"),
    os.path.join(HOME, "Library/Application Support/Google/Chrome/Default/Cache"),
    os.path.join(HOME, "Library/Application Support/Google/Chrome/Default/Code Cache"),
    os.path.join(HOME, "Library/Safari/Cache"),
    os.path.join(HOME, "Library/Containers/com.apple.Safari/Data/Library/Caches"),
    os.path.join(HOME, "Library/Containers/com.apple.mail/Data/Library/Caches"),
]

TEMP_CACHE_DIRS = ["/tmp", "/private/var/folders"]
OPTIONAL_CACHE_DIRS = [
    os.path.join(HOME, "Library/Containers/com.apple.Photos/Data/Library/Caches"),
]

# Developer caches — large, fully regenerated on next build/install
DEV_CACHE_DIRS = [
    os.path.join(HOME, "Library/Developer/Xcode/DerivedData"),
    os.path.join(HOME, ".npm/_cacache"),
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

DS_STORE_SKIP_DIRS = frozenset({
    ".Trash", ".git", ".hg", ".svn", ".venv", "Library", "node_modules",
    "vendor", "build", "dist",
})

OP_KEYS = frozenset({
    "caches", "dev", "ds_store", "logs", "mail", "trash", "quicklook",
    "dns", "ram", "language",
})

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
    def __init__(self, dry_run: bool = False, write_state: bool = True):
        if platform.system() != "Darwin":
            print(f"{C.RED}Error: This script only runs on macOS.{C.RESET}")
            sys.exit(1)

        self.dry_run = dry_run
        # Read-only modes must not create logs/configuration files. A dry run
        # is a preview, not a state-changing execution.
        self.write_state = write_state and not dry_run
        self._log_handle = None
        self._log_count = 0
        self.allowed_roots = [os.path.realpath(r) for r in ALLOWED_ROOTS_RAW]
        self.stats = {"total_size": 0, "total_files": 0, "last_run": None}
        self._load_config()
        if self.write_state:
            self._ensure_log_dir()
            self._log("Session started")
            atexit.register(self.close)

    # ── Config ───────────────────────────────────────────────────────────

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if isinstance(data.get("total_size"), (int, float)):
                        self.stats["total_size"] = max(0.0, float(data["total_size"]))
                    if isinstance(data.get("total_files"), int):
                        self.stats["total_files"] = max(0, data["total_files"])
                    if data.get("last_run") is None or isinstance(data.get("last_run"), (int, float)):
                        self.stats["last_run"] = data.get("last_run")
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

    def _save_config(self):
        if not self.write_state:
            return
        self.stats["last_run"] = time.time()
        temp_path = f"{CONFIG_PATH}.{os.getpid()}.tmp"
        try:
            with open(temp_path, "w") as f:
                json.dump(self.stats, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, CONFIG_PATH)
        except (OSError, TypeError, ValueError):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            pass

    # ── Logging ──────────────────────────────────────────────────────────

    def _ensure_log_dir(self):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
                os.replace(LOG_PATH, LOG_PATH + ".1")
            self._log_handle = open(LOG_PATH, "a", encoding="utf-8")
        except OSError:
            self._log_handle = None

    def _log(self, msg: str):
        if self._log_handle is None:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._log_handle.write(f"[{ts}] {msg}\n")
            self._log_count += 1
            if self._log_count % 100 == 0:
                self._log_handle.flush()
        except OSError:
            pass

    def close(self):
        """Close the per-run log handle, if logging is enabled."""
        if self._log_handle is not None:
            try:
                self._log_handle.flush()
                self._log_handle.close()
            except OSError:
                pass
            finally:
                self._log_handle = None

    # ── Safety ───────────────────────────────────────────────────────────

    def _is_realpath_allowed(self, real: str) -> bool:
        """Check a resolved path against the configured roots."""
        for root in self.allowed_roots:
            try:
                if os.path.commonpath((real, root)) == root:
                    return True
            except ValueError:
                continue
        return False

    def _is_within_allowed(self, path: str) -> bool:
        """Check whether a path resolves inside an allowed root."""
        try:
            return self._is_realpath_allowed(os.path.realpath(path))
        except OSError:
            return False

    def _is_safe(self, path: str) -> bool:
        """Check path is within allowed roots and is not a root itself."""
        try:
            real = os.path.realpath(path)
            return real not in self.allowed_roots and self._is_realpath_allowed(real)
        except OSError:
            return False

    # ── Deletion ─────────────────────────────────────────────────────────

    def _safe_delete(self, path: str, size_mb: float = 0) -> bool:
        """Unified safe deletion for files and directories."""
        try:
            entry_stat = os.lstat(path)
        except OSError:
            return False

        # lstat + unlink/rmtree prevents a symlink from being followed if a
        # path changes between the scan and deletion.
        if stat.S_ISLNK(entry_stat.st_mode):
            return False
        if not self._is_safe(path):
            self._log(f"Blocked: {path}")
            return False

        label = f"{size_mb:.2f} MB" if size_mb > 0.01 else "<0.01 MB"

        if self.dry_run:
            self._log(f"[DRY] {path} ({label})")
            return True

        try:
            if stat.S_ISDIR(entry_stat.st_mode):
                shutil.rmtree(path)
            else:
                os.remove(path)
            if os.path.lexists(path):
                raise OSError("path still exists after deletion")
            self._log(f"Deleted: {path} ({label})")
            return True
        except OSError as e:
            self._log(f"Failed: {path}: {e}")
            return False

    # ── Scanning ─────────────────────────────────────────────────────────

    def _scan_files(self, directory: str, min_age_s: float = 0):
        """Fast recursive scanner using os.scandir. Yields (path, size_mb)."""
        if not self._is_within_allowed(directory) or not os.path.isdir(directory):
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

    def _tree_size(self, directory: str) -> float:
        """Return the size of a tree without following symbolic links."""
        total = 0
        stack = [directory]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            else:
                                total += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue
            except OSError:
                continue
        return total / MB

    # ── Cleanup Operations ───────────────────────────────────────────────

    def clean_caches(
        self, days: int = DEFAULT_DAYS, include_temp: bool = False,
        include_optional: bool = False,
        verbose: bool = False,
    ) -> tuple:
        """Clean known caches older than ``days`` without following symlinks."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        cache_dirs = list(CACHE_DIRS)
        if include_optional:
            cache_dirs += OPTIONAL_CACHE_DIRS
        if include_temp:
            cache_dirs += TEMP_CACHE_DIRS
        for d in cache_dirs:
            if not os.path.isdir(d):
                continue
            if verbose:
                print(f"  {C.DIM}Scanning {d}{C.RESET}")

            for path, size in self._scan_files(d, min_age_s=age_s):
                if self._safe_delete(path, size):
                    total_files += 1
                    total_mb += size

        return total_files, total_mb

    def clean_dev_caches(self, days: int = DEFAULT_DAYS, verbose: bool = False) -> tuple:
        """Clean developer caches not already covered by ``clean_caches``."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        for d in DEV_CACHE_DIRS:
            if not os.path.isdir(d):
                continue
            if verbose:
                print(f"  {C.DIM}Cleaning {d}{C.RESET}")

            for path, size in self._scan_files(d, min_age_s=age_s):
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
                            if (entry.name not in DS_STORE_SKIP_DIRS
                                    and not entry.name.startswith(".")):
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

    def clean_logs(self, days: int = MIN_LOG_DAYS, verbose: bool = False) -> tuple:
        """Clean diagnostic reports older than ``days``."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        for d in LOG_CLEANUP_DIRS:
            if not os.path.isdir(d):
                continue
            if verbose:
                print(f"  {C.DIM}Cleaning {d}{C.RESET}")

            for path, size in self._scan_files(d, min_age_s=age_s):
                if self._safe_delete(path, size):
                    total_files += 1
                    total_mb += size

        return total_files, total_mb

    def clean_mail(self, days: int = MIN_MAIL_DAYS, verbose: bool = False) -> tuple:
        """Clean old Mail.app attachment copies, leaving recent downloads."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        if not os.path.isdir(MAIL_DOWNLOADS):
            return total_files, total_mb

        if verbose:
            print(f"  {C.DIM}Cleaning {MAIL_DOWNLOADS}{C.RESET}")

        for path, size in self._scan_files(MAIL_DOWNLOADS, min_age_s=age_s):
            if self._safe_delete(path, size):
                total_files += 1
                total_mb += size

        return total_files, total_mb

    def clean_language_files(self, verbose: bool = False) -> tuple:
        """Remove non-English resources only from unsigned user applications."""
        if self.dry_run:
            self._log("[DRY] Language cleanup skipped")
            return 0, 0.0
        keep = frozenset({"en.lproj", "en_US.lproj", "English.lproj", "Base.lproj"})
        try:
            current_locale = locale.getlocale()[0]
        except ValueError:
            current_locale = None
        if current_locale:
            locale_names = {current_locale, current_locale.replace("-", "_")}
            locale_names.add(current_locale.split("_")[0])
            keep = keep.union(f"{name}.lproj" for name in locale_names)
        search_root = os.path.join(HOME, "Applications")
        total_files = 0
        total_mb = 0.0

        if not os.path.isdir(search_root):
            return total_files, total_mb
        if verbose:
            print(f"  {C.DIM}Scanning {search_root}{C.RESET}")

        try:
            app_entries = list(os.scandir(search_root))
        except OSError:
            return total_files, total_mb

        for app in app_entries:
            if (not app.is_dir(follow_symlinks=False)
                    or not app.name.endswith(".app")
                    or self._is_signed_bundle(app.path)):
                continue
            resources = os.path.join(app.path, "Contents", "Resources")
            try:
                with os.scandir(resources) as it:
                    for entry in it:
                        if (not entry.is_dir(follow_symlinks=False)
                                or not entry.name.endswith(".lproj")
                                or entry.name in keep):
                            continue
                        size = self._tree_size(entry.path)
                        if self._safe_delete(entry.path, size):
                            total_files += 1
                            total_mb += size
            except OSError:
                continue

        return total_files, total_mb

    def _is_signed_bundle(self, app_path: str) -> bool:
        """Return whether an application has a code signature.

        Failure to inspect a bundle is treated as signed/unsafe so an
        unavailable or broken ``codesign`` tool can never widen deletion.
        """
        try:
            subprocess.run(
                ["codesign", "--display", "--verbose=2", app_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return True
        except subprocess.CalledProcessError:
            return False
        except (OSError, subprocess.SubprocessError):
            return True

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
                            size = self._tree_size(entry.path)
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
        if self.dry_run:
            self._log("[DRY] DNS cache flush skipped")
            return False
        if verbose:
            print(f"  {C.DIM}Flushing DNS cache...{C.RESET}")

        flushed = False
        try:
            subprocess.run(
                ["dscacheutil", "-flushcache"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            flushed = True
        except (OSError, subprocess.SubprocessError):
            pass

        # Use osascript for the sudo commands — shows a native password dialog
        script = 'do shell script "killall -HUP mDNSResponder 2>/dev/null" with administrator privileges'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            flushed = True
            self._log("DNS cache flushed")
        except (OSError, subprocess.SubprocessError):
            self._log("DNS flush: user cancelled or failed")
        return flushed

    def free_ram(self, verbose: bool = False) -> bool:
        """Actually purge inactive memory (prompts for admin password via GUI dialog)."""
        if self.dry_run:
            self._log("[DRY] Memory purge skipped")
            return False
        if verbose:
            print(f"  {C.DIM}Purging inactive memory...{C.RESET}")

        script = 'do shell script "purge" with administrator privileges'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            self._log("Memory purged")
            return True
        except (OSError, subprocess.SubprocessError):
            self._log("Memory purge: user cancelled or failed")
            return False

    def reset_quicklook(self, verbose: bool = False) -> bool:
        """Reset the QuickLook thumbnail cache."""
        if self.dry_run:
            self._log("[DRY] QuickLook reset skipped")
            return False
        if verbose:
            print(f"  {C.DIM}Resetting QuickLook cache...{C.RESET}")

        try:
            subprocess.run(
                ["qlmanage", "-r", "cache"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            self._log("QuickLook cache reset")
            return True
        except (OSError, subprocess.SubprocessError):
            self._log("QuickLook reset failed")
            return False

    def find_large_files(
        self, min_size_mb: float = 100, limit: int = 50
    ) -> list:
        """Find the largest files in the home directory using a min-heap."""
        if min_size_mb < 0:
            raise ValueError("min_size_mb must be non-negative")
        if limit <= 0:
            return []
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
                        if entry.name.startswith("."):
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
            "cpu_pct": psutil.cpu_percent(interval=0.1),
            "last_run": self.stats.get("last_run"),
            "total_cleaned_mb": self.stats.get("total_size", 0),
            "total_cleaned_files": self.stats.get("total_files", 0),
        }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _bar(pct: float, w: int = 20) -> str:
    pct = max(0.0, min(100.0, pct))
    f = int(pct / 100 * w)
    color = C.GREEN if pct < 60 else C.YELLOW if pct < 85 else C.RED
    return f"{color}{'█' * f}{C.DIM}{'░' * (w - f)}{C.RESET}"


def _sz(mb: float) -> str:
    if mb < 0.01:
        return "—"
    return f"{mb:.1f} MB" if mb < 1024 else f"{mb / 1024:.2f} GB"


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser separately so validation can be tested."""
    parser = argparse.ArgumentParser(description="Lean macOS system cleaner")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview without deletion, admin prompts or state writes",
    )
    parser.add_argument(
        "--days", type=_non_negative_int, default=DEFAULT_DAYS,
        help=f"Cache age threshold in days (default: {DEFAULT_DAYS})",
    )
    parser.add_argument("--aggressive", action="store_true", help="Clean languages in unsigned user apps")
    parser.add_argument(
        "--full", action="store_true",
        help="Include Trash, DNS and QuickLook maintenance operations",
    )
    parser.add_argument(
        "--purge-memory", action="store_true",
        help="Explicitly purge inactive memory with an admin prompt",
    )
    parser.add_argument(
        "--skip", nargs="+", default=[], metavar="OP", choices=sorted(OP_KEYS),
        help="Skip selected operations",
    )
    parser.add_argument("--analyze", action="store_true", help="Find large files only")
    parser.add_argument("--info", action="store_true", help="Show system info only")
    parser.add_argument("-v", "--verbose", action="store_true", help="Detailed progress")
    return parser


def main():
    args = build_parser().parse_args()

    skip = set(args.skip)
    cleaner = SystemCleanup(
        dry_run=args.dry_run,
        write_state=not (args.dry_run or args.info or args.analyze),
    )

    # ── Header & system info ─────────────────────────────────────────────
    if args.info or args.analyze:
        mode = f"{C.CYAN}READ ONLY{C.RESET}"
    elif args.dry_run:
        mode = f"{C.YELLOW}DRY RUN{C.RESET}"
    else:
        mode = f"{C.GREEN}LIVE{C.RESET}"
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
    # (key, label, method_name, method_kwargs, enabled, side_effect)
    ops = [
        (
            "caches", "System & app caches", "clean_caches",
            {"days": args.days, "include_temp": args.full, "include_optional": args.full},
            True, False,
        ),
        ("dev", "Developer caches", "clean_dev_caches", {"days": args.days}, True, False),
        ("ds_store", ".DS_Store files", "clean_ds_store", {}, True, False),
        (
            "logs", "Old crash reports", "clean_logs",
            {"days": max(args.days, MIN_LOG_DAYS)}, True, False,
        ),
        (
            "mail", "Old Mail downloads", "clean_mail",
            {"days": max(args.days, MIN_MAIL_DAYS)}, True, False,
        ),
        ("trash", "Trash", "empty_trash", {}, args.full, False),
        ("quicklook", "QuickLook cache", "reset_quicklook", {}, args.full, True),
        ("dns", "DNS cache", "flush_dns", {}, args.full, True),
        ("ram", "Inactive memory", "free_ram", {}, args.purge_memory, True),
        (
            "language", "Language files", "clean_language_files", {},
            args.aggressive, True,
        ),
    ]

    results = []
    for key, label, method, kwargs, enabled, side_effect in ops:
        if key in skip:
            continue
        if not enabled:
            continue
        if args.dry_run and side_effect:
            print(f"  {C.SKIP} {label} (skipped in dry-run)")
            continue

        print(f"  {C.DOT} {label}")
        try:
            ret = getattr(cleaner, method)(verbose=args.verbose, **kwargs)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            cleaner._log(f"Operation failed: {label}: {type(exc).__name__}")
            print(f"  {C.RED}Failed: {label}{C.RESET}")
            results.append((label, 0, 0.0))
            continue

        # Methods return (files, mb) or bool
        if isinstance(ret, tuple):
            results.append((label, ret[0], ret[1]))
        else:
            results.append((label, 1 if ret else 0, 0.0))

    # ── Results ──────────────────────────────────────────────────────────
    W = 58
    print(f"\n  {C.DIM}{'─' * W}{C.RESET}")
    print(f"  {C.BOLD}{'':>2}{'Operation':<30} {'Items':>7}  {'Freed':>12}{C.RESET}")
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

    if not args.dry_run:
        cleaner.stats.update({"total_files": total_f, "total_size": total_mb})
        cleaner._save_config()

    if args.dry_run:
        print(f"\n  {C.YELLOW}Dry run — nothing was deleted.{C.RESET}")
    print()


if __name__ == "__main__":
    main()
