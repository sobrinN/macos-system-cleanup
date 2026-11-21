#!/usr/bin/env python3
"""Mac Clean — single-file, zero-dependency macOS system & browser-game cleaner.

Double-click in Finder (or run with no arguments) to launch the interactive
Terminal UI, or pass CLI flags (e.g. ``--analyze``, ``--dry-run``, ``--full``)
for direct command-line execution.
"""

from __future__ import annotations

import sys

# Avoid creating a __pycache__ folder in Finder when running this single file.
sys.dont_write_bytecode = True

import argparse
import atexit
from dataclasses import dataclass
import heapq
import json
import locale
import os
from pathlib import Path
import platform
import re
import select
import shutil
import sqlite3
import stat
import subprocess
import termios
import textwrap
import time
import tty
from typing import Iterable, Optional
from urllib.parse import quote

try:
    import psutil  # Optional — native macOS fallback used when unavailable
except ImportError:
    psutil = None

# ── Constants ────────────────────────────────────────────────────────────────

VERSION = "2.0.0"
HOME = str(Path.home())
MB = 1024 * 1024
GB = 1024 ** 3
DAY = 86400
DEFAULT_DAYS = 7
DEFAULT_BROWSER_GAME_MIN_MB = 100
MIN_LOG_DAYS = 30
MIN_MAIL_DAYS = 30
LOG_MAX_BYTES = 5 * MB

CONFIG_PATH = os.path.join(HOME, ".system_cleanup_config.json")
LOG_DIR = os.path.join(HOME, "Library/Logs/SystemCleanup")
LOG_PATH = os.path.join(LOG_DIR, "cleanup.log")

ALLOWED_ROOTS_RAW = [HOME, "/tmp", "/private/var/folders"]

SKIP_DIRS = frozenset({
    ".Trash", "Keychains", "Preferences", "Extensions",
    "CloudStorage", "Mobile Documents",
})
SKIP_FILES = frozenset({"com.apple.dock.plist", "com.apple.finder.plist"})

# Directories that are purely cache — safe to wipe old contents
CACHE_DIRS = [
    os.path.join(HOME, "Library/Caches"),
    os.path.join(HOME, "Library/Containers/com.apple.Safari/Data/Library/Caches"),
    os.path.join(HOME, "Library/Containers/com.apple.mail/Data/Library/Caches"),
]

TEMP_CACHE_DIRS = ["/tmp", "/private/var/folders"]
OPTIONAL_CACHE_DIRS = [
    os.path.join(HOME, "Library/Containers/com.apple.Photos/Data/Library/Caches"),
]

# Developer caches — large, fully regenerable on next build/install
DEV_CACHE_DIRS = [
    os.path.join(HOME, "Library/Developer/Xcode/DerivedData"),
    os.path.join(HOME, "Library/Developer/CoreSimulator/Caches"),
    os.path.join(HOME, ".npm/_cacache"),
    os.path.join(HOME, ".bun/install/cache"),
    os.path.join(HOME, ".cache/pip"),
    os.path.join(HOME, ".cache/uv"),
    os.path.join(HOME, ".cache/yarn"),
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

LARGE_FILE_ALLOWED_DOTDIRS = frozenset({
    ".npm", ".gradle", ".cache", ".cargo", ".bun", ".composer", ".local",
})

# Browser registry: (display_name, app_support_rel, caches_rel, process_name)
CHROMIUM_BROWSERS = (
    ("Google Chrome", "Google/Chrome", "Google/Chrome", "Google Chrome"),
    ("Chrome Beta", "Google/Chrome Beta", "Google/Chrome Beta", "Google Chrome Beta"),
    ("Chrome Canary", "Google/Chrome Canary", "Google/Chrome Canary", "Google Chrome Canary"),
    ("Brave", "BraveSoftware/Brave-Browser", "BraveSoftware/Brave-Browser", "Brave Browser"),
    ("Microsoft Edge", "Microsoft Edge", "Microsoft Edge", "Microsoft Edge"),
    ("Arc", "Arc/User Data", "Arc", "Arc"),
    ("Vivaldi", "Vivaldi", "Vivaldi", "Vivaldi"),
    ("Opera", "com.operasoftware.Opera", "com.operasoftware.Opera", "Opera"),
    ("Chromium", "Chromium", "Chromium", "Chromium"),
)

# Regenerable Chromium cache subdirs inside each profile
CHROMIUM_PROFILE_CACHE_SUBDIRS = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnWebGPUCache",
    "DawnGraphiteCache",
    "GrShaderCache",
    "Media Cache",
    "Storage",
    "image_cache",
    os.path.join("Service Worker", "ScriptCache"),
)

# Browser-root level shader/crash caches (outside profiles)
CHROMIUM_ROOT_CACHE_SUBDIRS = (
    "GrShaderCache",
    "ShaderCache",
    "GraphiteDawnCache",
    os.path.join("Crashpad", "completed"),
    os.path.join("Crashpad", "pending"),
)

# Chrome updater CRX download cache (often 500 MB – 1+ GB of old update blobs)
CHROME_UPDATER_CRX_CACHE = os.path.join(
    HOME, "Library/Application Support/Google/GoogleUpdater/crx_cache"
)
CHROME_UPDATER_KEEP_FILES = frozenset({"metadata.json"})

SAFARI_CACHE_DIRS = (
    os.path.join(HOME, "Library/Caches/com.apple.Safari"),
    os.path.join(HOME, "Library/Safari/Cache"),
    os.path.join(HOME, "Library/Containers/com.apple.Safari/Data/Library/Caches"),
)

FIREFOX_CACHES_BASE = os.path.join(HOME, "Library/Caches/Firefox/Profiles")
FIREFOX_APP_SUPPORT_BASE = os.path.join(HOME, "Library/Application Support/Firefox/Profiles")

OP_KEYS = frozenset({
    "caches", "browsers", "browser_games", "dev", "ds_store",
    "logs", "mail", "trash", "quicklook", "dns", "ram", "language",
})

_URL_RE = re.compile(rb"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
_PROFILE_NAME_RE = re.compile(r"^(Default|Guest Profile|System Profile|Profile \d+)$")


@dataclass
class BrowserGameItem:
    """A discrete browser game / heavy web-storage artifact."""
    browser: str
    profile: str
    kind: str           # IndexedDB, WebStorage/OPFS, CacheStorage, File System, WASM Cache, etc.
    origin: str         # Human-readable domain/origin or bucket label
    paths: list[str]    # One or more paths deleted atomically together
    size_mb: float
    process_name: str
    is_app_support: bool = True

    def to_dict(self) -> dict:
        return {
            "browser": self.browser,
            "profile": self.profile,
            "kind": self.kind,
            "origin": self.origin,
            "paths": self.paths,
            "size_mb": round(self.size_mb, 2),
        }


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
    BLUE = "\033[34m" if _on else ""
    RESET = "\033[0m" if _on else ""
    OK = "\033[32m✓\033[0m" if _on else "[OK]"
    WARN = "\033[33m!\033[0m" if _on else "[!]"
    SKIP = "\033[2m⊘\033[0m" if _on else "[-]"
    DOT = "\033[36m●\033[0m" if _on else "*"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _c_env() -> dict[str, str]:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    return env


def _stat_size_bytes(st: os.stat_result) -> int:
    """Return accurate disk size, accounting for APFS sparse files."""
    logical = st.st_size
    blocks = getattr(st, "st_blocks", 0)
    if blocks > 0:
        allocated = blocks * 512
        if allocated < logical:
            return allocated
    return logical


def _parse_indexeddb_origin(dirname: str) -> str:
    """Turn 'https_crazygames.com_0.indexeddb.leveldb' into 'https://crazygames.com'."""
    base = dirname
    for suffix in (".indexeddb.leveldb", ".indexeddb.blob"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    m = re.match(r"^(https?)_(.+)_(\d+)$", base)
    if m:
        scheme, host, port = m.group(1), m.group(2), m.group(3)
        if port == "0":
            return f"{scheme}://{host}"
        return f"{scheme}://{host}:{port}"
    return base


def _parse_firefox_origin(dirname: str) -> str:
    """Turn 'https+++crazygames.com' into 'https://crazygames.com'."""
    if "+++" in dirname:
        scheme, rest = dirname.split("+++", 1)
        host = rest.split("^", 1)[0].replace("+", ":")
        return f"{scheme}://{host}"
    return dirname


def _shorten_path(path: str) -> str:
    if path == HOME:
        return "~"
    prefix = HOME + os.sep
    if path.startswith(prefix):
        return "~/" + path[len(prefix):]
    return path


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
        self.permission_denied: list[str] = []
        self._permission_denied_set: set[str] = set()
        self.warnings: list[str] = []
        self._running_processes_cache: Optional[set[str]] = None
        self._claimed_dirs: set[str] = set()
        self.stats = {
            "total_size": 0.0,
            "total_files": 0,
            "last_size": 0.0,
            "last_files": 0,
            "last_run": None,
        }
        self._load_config()
        if self.write_state:
            self._ensure_log_dir()
            self._log("Session started")
            atexit.register(self.close)

    # ── Diagnostics Tracking ─────────────────────────────────────────────

    def _record_permission_denied(self, path: str) -> None:
        if not hasattr(self, "_permission_denied_set"):
            self._permission_denied_set = set()
            self.permission_denied = []
        norm = os.path.normpath(path)
        # Collapse child denials under an already-recorded parent denial
        for existing in self._permission_denied_set:
            if norm == existing or norm.startswith(existing + os.sep):
                return
        self._permission_denied_set.add(norm)
        self.permission_denied.append(norm)
        self._log(f"Permission denied (TCC/OS): {norm}")

    def _record_warning(self, msg: str) -> None:
        if not hasattr(self, "warnings"):
            self.warnings = []
        if msg not in self.warnings:
            self.warnings.append(msg)
            self._log(f"Warning: {msg}")

    # ── Config ───────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if isinstance(data.get("total_size"), (int, float)):
                        self.stats["total_size"] = max(0.0, float(data["total_size"]))
                    if isinstance(data.get("total_files"), int):
                        self.stats["total_files"] = max(0, data["total_files"])
                    if isinstance(data.get("last_size"), (int, float)):
                        self.stats["last_size"] = max(0.0, float(data["last_size"]))
                    if isinstance(data.get("last_files"), int):
                        self.stats["last_files"] = max(0, data["last_files"])
                    if data.get("last_run") is None or isinstance(data.get("last_run"), (int, float)):
                        self.stats["last_run"] = data.get("last_run")
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass

    def _save_config(self) -> None:
        if not self.write_state:
            return
        self.stats["last_run"] = time.time()
        temp_path = f"{CONFIG_PATH}.{os.getpid()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
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

    # ── Logging ──────────────────────────────────────────────────────────

    def _ensure_log_dir(self) -> None:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
                os.replace(LOG_PATH, LOG_PATH + ".1")
            self._log_handle = open(LOG_PATH, "a", encoding="utf-8")
        except OSError:
            self._log_handle = None

    def _log(self, msg: str) -> None:
        if self._log_handle is None:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._log_handle.write(f"[{ts}] {msg}\n")
            self._log_count = getattr(self, "_log_count", 0) + 1
            if self._log_count % 100 == 0:
                self._log_handle.flush()
        except OSError:
            pass

    def close(self) -> None:
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

    def _dedupe_dirs(self, dirs: Iterable[str]) -> list[str]:
        """Deduplicate and prune directories already contained by another target."""
        resolved: list[tuple[str, str]] = []
        seen_real: set[str] = set()
        for d in dirs:
            if not d:
                continue
            try:
                if not os.path.isdir(d) or os.path.islink(d):
                    continue
                real = os.path.realpath(d)
            except PermissionError:
                self._record_permission_denied(d)
                continue
            except OSError:
                continue
            if real in seen_real:
                continue
            seen_real.add(real)
            resolved.append((real, d))

        # Sort shallowest first so parent directories precede children
        resolved.sort(key=lambda item: (item[0].count(os.sep), len(item[0])))
        kept: list[tuple[str, str]] = []
        for real, orig in resolved:
            if any(real == parent or real.startswith(parent + os.sep) for parent, _ in kept):
                continue
            kept.append((real, orig))
        return [orig for _, orig in kept]

    # ── Deletion & Pruning ───────────────────────────────────────────────

    def _safe_delete(self, path: str, size_mb: float = 0) -> bool:
        """Unified safe deletion for files and directories."""
        try:
            entry_stat = os.lstat(path)
        except PermissionError:
            self._record_permission_denied(path)
            return False
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
        except PermissionError:
            self._record_permission_denied(path)
            return False
        except OSError as e:
            self._log(f"Failed: {path}: {e}")
            return False

    def _prune_empty_dirs(self, root_dir: str) -> int:
        """Remove newly empty subdirectories under ``root_dir`` (never root itself)."""
        if self.dry_run or not self._is_within_allowed(root_dir):
            return 0
        removed = 0
        try:
            root_real = os.path.realpath(root_dir)
        except OSError:
            return 0

        # Collect subdirectories depth-first, then remove deepest first
        dirs: list[str] = []
        stack = [root_dir]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                                continue
                            dirs.append(entry.path)
                            stack.append(entry.path)
            except OSError:
                continue

        for d in reversed(dirs):
            try:
                if os.path.realpath(d) == root_real or not self._is_safe(d):
                    continue
                os.rmdir(d)
                removed += 1
            except OSError:
                continue
        return removed

    # ── Scanning ─────────────────────────────────────────────────────────

    def _scan_files(
        self,
        directory: str,
        min_age_s: float = 0,
        exclude_realpaths: Optional[set[str]] = None,
        skip_names: Optional[frozenset[str]] = None,
    ):
        """Fast recursive scanner using os.scandir. Yields (path, size_mb)."""
        if not self._is_within_allowed(directory) or not os.path.isdir(directory):
            return

        now = time.time()
        stack = [directory]
        excluded = exclude_realpaths or set()
        extra_skip = skip_names or frozenset()

        while stack:
            current = stack.pop()
            if excluded:
                try:
                    if os.path.realpath(current) in excluded:
                        continue
                except OSError:
                    continue
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
                        if name.startswith(".") or name in SKIP_FILES or name in extra_skip:
                            continue

                        try:
                            st = entry.stat(follow_symlinks=False)
                            if min_age_s > 0 and (now - st.st_mtime) < min_age_s:
                                continue
                            yield entry.path, _stat_size_bytes(st) / MB
                        except PermissionError:
                            if current == directory:
                                self._record_permission_denied(entry.path)
                            continue
                        except OSError:
                            continue
            except PermissionError:
                if current == directory:
                    self._record_permission_denied(current)
                continue
            except OSError:
                continue

    def _tree_size(self, directory: str) -> float:
        """Return the size (MB) of a tree without following symbolic links."""
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
                                total += _stat_size_bytes(entry.stat(follow_symlinks=False))
                        except OSError:
                            continue
            except PermissionError:
                if current == directory:
                    self._record_permission_denied(current)
                continue
            except OSError:
                continue
        return total / MB

    # ── Process & Browser Discovery ──────────────────────────────────────

    def _is_process_running(self, process_name: str) -> bool:
        """Return True if a macOS app process with ``process_name`` is running."""
        if not process_name:
            return False
        if self._running_processes_cache is None:
            try:
                out = subprocess.check_output(
                    ["ps", "-A", "-o", "comm="],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                names: set[str] = set()
                for line in out.splitlines():
                    base = os.path.basename(line.strip())
                    if base:
                        names.add(base)
                self._running_processes_cache = names
            except (OSError, subprocess.SubprocessError):
                self._running_processes_cache = set()
        return process_name in self._running_processes_cache

    def _discover_chromium_profiles(self, base_dir: str) -> list[tuple[str, str]]:
        """Return list of (profile_name, profile_path) under a Chromium root."""
        if not os.path.isdir(base_dir):
            return []
        profiles: list[tuple[str, str]] = []
        try:
            with os.scandir(base_dir) as it:
                for entry in it:
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    name = entry.name
                    if _PROFILE_NAME_RE.match(name) or os.path.isfile(
                        os.path.join(entry.path, "Preferences")
                    ):
                        profiles.append((name, entry.path))
        except PermissionError:
            self._record_permission_denied(base_dir)
            return []
        except OSError:
            return []
        profiles.sort(key=lambda item: item[0])
        return profiles

    def _load_chromium_quota_origins(self, profile_dir: str) -> dict[str, str]:
        """Read-only lookup of Chromium WebStorage bucket_id -> origin from QuotaManager."""
        qm_path = os.path.join(profile_dir, "WebStorage", "QuotaManager")
        if not os.path.isfile(qm_path):
            return {}
        mapping: dict[str, str] = {}
        conn = None
        try:
            uri = f"file:{quote(qm_path)}?mode=ro&immutable=1"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT id, storage_key FROM buckets")
            for bucket_id, storage_key in cur.fetchall():
                if not storage_key:
                    continue
                origin = str(storage_key).rstrip("/")
                # Strip Chromium storage key partition suffixes like '^0...'
                if "^" in origin:
                    origin = origin.split("^", 1)[0].rstrip("/")
                mapping[str(bucket_id)] = origin
        except (sqlite3.Error, OSError):
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        return mapping

    def _extract_cache_storage_origin(self, hash_dir: str) -> str:
        """Best-effort extract origin URL from Chromium CacheStorage index.txt."""
        index_path = os.path.join(hash_dir, "index.txt")
        try:
            if os.path.isfile(index_path):
                with open(index_path, "rb") as f:
                    raw = f.read(4096)
                m = _URL_RE.search(raw)
                if m:
                    return m.group(0).decode("utf-8", errors="ignore").rstrip("/")
        except OSError:
            pass
        return f"cache-{os.path.basename(hash_dir)[:12]}"

    def _browser_cache_roots_for_exclusion(self) -> set[str]:
        """Return resolved browser dirs inside ~/Library/Caches handled by clean_browser_caches."""
        roots: set[str] = set()
        caches_home = os.path.join(HOME, "Library/Caches")
        for _, _, caches_rel, _ in CHROMIUM_BROWSERS:
            p = os.path.join(caches_home, caches_rel)
            if os.path.isdir(p):
                try:
                    roots.add(os.path.realpath(p))
                except OSError:
                    pass
        for p in (FIREFOX_CACHES_BASE, *SAFARI_CACHE_DIRS):
            if os.path.isdir(p):
                try:
                    roots.add(os.path.realpath(p))
                except OSError:
                    pass
        return roots

    # ── Cleanup Operations ───────────────────────────────────────────────

    def clean_caches(
        self,
        days: int = DEFAULT_DAYS,
        include_temp: bool = False,
        include_optional: bool = False,
        exclude_dirs: Optional[Iterable[str]] = None,
        verbose: bool = False,
    ) -> tuple[int, float]:
        """Clean known system and app caches older than ``days`` without following symlinks."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        raw_dirs = list(CACHE_DIRS)
        if include_optional:
            raw_dirs += OPTIONAL_CACHE_DIRS
        if include_temp:
            raw_dirs += TEMP_CACHE_DIRS

        cache_dirs = self._dedupe_dirs(raw_dirs)
        excluded_real = set()
        if exclude_dirs:
            for d in exclude_dirs:
                try:
                    excluded_real.add(os.path.realpath(d))
                except OSError:
                    pass

        for d in cache_dirs:
            if verbose:
                print(f"  {C.DIM}Scanning {_shorten_path(d)}{C.RESET}")

            deleted_in_dir = 0
            for path, size in self._scan_files(d, min_age_s=age_s, exclude_realpaths=excluded_real):
                if self._safe_delete(path, size):
                    total_files += 1
                    deleted_in_dir += 1
                    total_mb += size
            if deleted_in_dir > 0 and not self.dry_run:
                self._prune_empty_dirs(d)

        return total_files, total_mb

    def clean_browser_caches(
        self,
        days: int = DEFAULT_DAYS,
        force_browsers: bool = False,
        verbose: bool = False,
    ) -> tuple[int, float]:
        """Clean regenerable HTTP, Code, GPU/WebGPU and updater caches across browsers."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        targets: list[tuple[str, str, Optional[frozenset[str]], bool]] = []
        # (browser_name, path, skip_file_names, require_browser_closed)

        app_support_home = os.path.join(HOME, "Library/Application Support")
        caches_home = os.path.join(HOME, "Library/Caches")

        for name, app_rel, cache_rel, proc_name in CHROMIUM_BROWSERS:
            running = self._is_process_running(proc_name)

            # 1) ~/Library/Caches/<Browser> (HTTP Cache, Code Cache, Storage)
            cache_root = os.path.join(caches_home, cache_rel)
            for _, prof_path in self._discover_chromium_profiles(cache_root):
                for sub in CHROMIUM_PROFILE_CACHE_SUBDIRS:
                    p = os.path.join(prof_path, sub)
                    if os.path.isdir(p):
                        targets.append((name, p, None, False))
            for sub in CHROMIUM_ROOT_CACHE_SUBDIRS:
                p = os.path.join(cache_root, sub)
                if os.path.isdir(p):
                    targets.append((name, p, None, False))

            # 2) ~/Library/Application Support/<Browser> (GPUCache, DawnWebGPUCache, ScriptCache)
            app_root = os.path.join(app_support_home, app_rel)
            if os.path.isdir(app_root):
                if running and not self.dry_run and not force_browsers:
                    # Still try to discover profiles so TCC denials are surfaced,
                    # but avoid mutating open App Support caches.
                    self._discover_chromium_profiles(app_root)
                else:
                    for _, prof_path in self._discover_chromium_profiles(app_root):
                        for sub in CHROMIUM_PROFILE_CACHE_SUBDIRS:
                            p = os.path.join(prof_path, sub)
                            if os.path.isdir(p):
                                targets.append((name, p, None, True))
                    for sub in CHROMIUM_ROOT_CACHE_SUBDIRS:
                        p = os.path.join(app_root, sub)
                        if os.path.isdir(p):
                            targets.append((name, p, None, True))

        # Chrome updater CRX download cache (often hundreds of MB)
        if os.path.isdir(CHROME_UPDATER_CRX_CACHE):
            targets.append(("GoogleUpdater", CHROME_UPDATER_CRX_CACHE, CHROME_UPDATER_KEEP_FILES, False))

        # Firefox caches
        if os.path.isdir(FIREFOX_CACHES_BASE):
            try:
                with os.scandir(FIREFOX_CACHES_BASE) as it:
                    for prof in it:
                        if prof.is_symlink() or not prof.is_dir(follow_symlinks=False):
                            continue
                        for sub in ("cache2", "startupCache", "thumbnails"):
                            p = os.path.join(prof.path, sub)
                            if os.path.isdir(p):
                                targets.append(("Firefox", p, None, False))
            except PermissionError:
                self._record_permission_denied(FIREFOX_CACHES_BASE)
            except OSError:
                pass

        # Safari caches
        for p in SAFARI_CACHE_DIRS:
            if os.path.isdir(p):
                targets.append(("Safari", p, None, False))

        seen_real: set[str] = set(getattr(self, "_claimed_dirs", set()))
        for browser_name, d, skip_names, _ in targets:
            try:
                real = os.path.realpath(d)
            except OSError:
                continue
            if real in seen_real or any(real.startswith(c + os.sep) for c in seen_real):
                continue
            seen_real.add(real)

            if verbose:
                print(f"  {C.DIM}Scanning [{browser_name}] {_shorten_path(d)}{C.RESET}")

            deleted_in_dir = 0
            for path, size in self._scan_files(
                d, min_age_s=age_s, exclude_realpaths=seen_real - {real}, skip_names=skip_names
            ):
                if self._safe_delete(path, size):
                    total_files += 1
                    deleted_in_dir += 1
                    total_mb += size
            if deleted_in_dir > 0 and not self.dry_run:
                self._prune_empty_dirs(d)

        return total_files, total_mb

    def scan_browser_game_storage(
        self,
        min_mb: float = DEFAULT_BROWSER_GAME_MIN_MB,
        origin_filter: Optional[str] = None,
    ) -> list[BrowserGameItem]:
        """Discover heavy browser game & web-app storage units across all profiles.

        Targets where multi-GB browser games (Unity WebGL, Unreal, Godot, WebGPU,
        WASM portals) store downloaded game files:
        - Chromium IndexedDB (.leveldb + .blob grouped atomically per origin)
        - Chromium WebStorage / OPFS quota buckets (resolved via read-only QuotaManager)
        - Chromium Service Worker CacheStorage (resolved via index.txt)
        - Chromium File System API & blob_storage
        - Chromium compiled WASM Code Cache, DawnWebGPUCache & large HTTP Caches
        - Firefox per-origin storage/default (IndexedDB / CacheStorage / OPFS)
        """
        if min_mb < 0:
            raise ValueError("min_mb must be non-negative")

        items: list[BrowserGameItem] = []
        needle = origin_filter.lower().strip() if origin_filter else None

        app_support_home = os.path.join(HOME, "Library/Application Support")
        caches_home = os.path.join(HOME, "Library/Caches")

        def maybe_add(item: BrowserGameItem) -> None:
            if item.size_mb < min_mb:
                return
            if needle and needle not in item.origin.lower() and needle not in item.kind.lower():
                return
            items.append(item)

        for browser_name, app_rel, cache_rel, proc_name in CHROMIUM_BROWSERS:
            # 1) Profile Application Support storage (IndexedDB, WebStorage/OPFS, CacheStorage, File System)
            app_root = os.path.join(app_support_home, app_rel)
            for prof_name, prof_path in self._discover_chromium_profiles(app_root):
                # 1a. IndexedDB (group .leveldb and .blob by origin so they delete atomically)
                idb_dir = os.path.join(prof_path, "IndexedDB")
                if os.path.isdir(idb_dir):
                    grouped: dict[str, list[str]] = {}
                    try:
                        with os.scandir(idb_dir) as it:
                            for entry in it:
                                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                                    continue
                                if entry.name.endswith((".indexeddb.leveldb", ".indexeddb.blob")):
                                    origin = _parse_indexeddb_origin(entry.name)
                                    grouped.setdefault(origin, []).append(entry.path)
                    except PermissionError:
                        self._record_permission_denied(idb_dir)
                    except OSError:
                        pass
                    for origin, paths in grouped.items():
                        sz = sum(self._tree_size(p) for p in paths)
                        maybe_add(BrowserGameItem(
                            browser=browser_name, profile=prof_name, kind="IndexedDB",
                            origin=origin, paths=sorted(paths), size_mb=sz,
                            process_name=proc_name, is_app_support=True,
                        ))

                # 1b. WebStorage / OPFS Quota Buckets
                ws_dir = os.path.join(prof_path, "WebStorage")
                if os.path.isdir(ws_dir):
                    quota_map = self._load_chromium_quota_origins(prof_path)
                    try:
                        with os.scandir(ws_dir) as it:
                            for entry in it:
                                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                                    continue
                                origin = quota_map.get(entry.name, f"bucket-{entry.name}")
                                sz = self._tree_size(entry.path)
                                maybe_add(BrowserGameItem(
                                    browser=browser_name, profile=prof_name, kind="WebStorage/OPFS",
                                    origin=origin, paths=[entry.path], size_mb=sz,
                                    process_name=proc_name, is_app_support=True,
                                ))
                    except PermissionError:
                        self._record_permission_denied(ws_dir)
                    except OSError:
                        pass

                # 1c. Service Worker CacheStorage (PWA & HTML5 game portals)
                cs_dir = os.path.join(prof_path, "Service Worker", "CacheStorage")
                if os.path.isdir(cs_dir):
                    try:
                        with os.scandir(cs_dir) as it:
                            for entry in it:
                                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                                    continue
                                origin = self._extract_cache_storage_origin(entry.path)
                                sz = self._tree_size(entry.path)
                                maybe_add(BrowserGameItem(
                                    browser=browser_name, profile=prof_name, kind="CacheStorage",
                                    origin=origin, paths=[entry.path], size_mb=sz,
                                    process_name=proc_name, is_app_support=True,
                                ))
                    except PermissionError:
                        self._record_permission_denied(cs_dir)
                    except OSError:
                        pass

                # 1d. Legacy File System API (used by WebGL games & emulators)
                fs_dir = os.path.join(prof_path, "File System")
                if os.path.isdir(fs_dir):
                    try:
                        with os.scandir(fs_dir) as it:
                            for entry in it:
                                if (entry.is_symlink() or not entry.is_dir(follow_symlinks=False)
                                        or entry.name == "Origins"):
                                    continue
                                sz = self._tree_size(entry.path)
                                maybe_add(BrowserGameItem(
                                    browser=browser_name, profile=prof_name, kind="File System",
                                    origin=f"fs-{entry.name}", paths=[entry.path], size_mb=sz,
                                    process_name=proc_name, is_app_support=True,
                                ))
                    except PermissionError:
                        self._record_permission_denied(fs_dir)
                    except OSError:
                        pass

                # 1e. Blob storage
                blob_dir = os.path.join(prof_path, "blob_storage")
                if os.path.isdir(blob_dir):
                    sz = self._tree_size(blob_dir)
                    maybe_add(BrowserGameItem(
                        browser=browser_name, profile=prof_name, kind="Blob Storage",
                        origin="(temporary blobs)", paths=[blob_dir], size_mb=sz,
                        process_name=proc_name, is_app_support=True,
                    ))

                # 1f. Compiled WebGPU shaders inside App Support
                for shader_dir_name, kind_label in (
                    ("DawnWebGPUCache", "WebGPU Cache"),
                    ("DawnGraphiteCache", "WebGPU Cache"),
                ):
                    sd = os.path.join(prof_path, shader_dir_name)
                    if os.path.isdir(sd):
                        sz = self._tree_size(sd)
                        maybe_add(BrowserGameItem(
                            browser=browser_name, profile=prof_name, kind=kind_label,
                            origin=f"({shader_dir_name})", paths=[sd], size_mb=sz,
                            process_name=proc_name, is_app_support=True,
                        ))

            # 2) Heavy Caches in ~/Library/Caches/<Browser> (recent multi-GB WASM & HTTP game caches)
            cache_root = os.path.join(caches_home, cache_rel)
            for prof_name, prof_path in self._discover_chromium_profiles(cache_root):
                for rel_sub, kind_label, origin_label in (
                    (os.path.join("Code Cache", "wasm"), "WASM Code Cache", "(compiled WASM games)"),
                    ("Cache", "HTTP Cache", "(browser HTTP cache)"),
                ):
                    sub_path = os.path.join(prof_path, rel_sub)
                    if os.path.isdir(sub_path):
                        sz = self._tree_size(sub_path)
                        maybe_add(BrowserGameItem(
                            browser=browser_name, profile=prof_name, kind=kind_label,
                            origin=origin_label, paths=[sub_path], size_mb=sz,
                            process_name=proc_name, is_app_support=False,
                        ))

        # 3) Firefox per-origin IndexedDB / CacheStorage / OPFS storage
        if os.path.isdir(FIREFOX_APP_SUPPORT_BASE):
            try:
                with os.scandir(FIREFOX_APP_SUPPORT_BASE) as it:
                    for prof in it:
                        if prof.is_symlink() or not prof.is_dir(follow_symlinks=False):
                            continue
                        storage_default = os.path.join(prof.path, "storage", "default")
                        if not os.path.isdir(storage_default):
                            continue
                        try:
                            with os.scandir(storage_default) as sit:
                                for origin_dir in sit:
                                    if (origin_dir.is_symlink()
                                            or not origin_dir.is_dir(follow_symlinks=False)):
                                        continue
                                    origin = _parse_firefox_origin(origin_dir.name)
                                    sz = self._tree_size(origin_dir.path)
                                    maybe_add(BrowserGameItem(
                                        browser="Firefox", profile=prof.name, kind="Origin Storage",
                                        origin=origin, paths=[origin_dir.path], size_mb=sz,
                                        process_name="firefox", is_app_support=True,
                                    ))
                        except PermissionError:
                            self._record_permission_denied(storage_default)
                        except OSError:
                            pass
            except PermissionError:
                self._record_permission_denied(FIREFOX_APP_SUPPORT_BASE)
            except OSError:
                pass

        items.sort(key=lambda item: item.size_mb, reverse=True)
        return items

    def clean_browser_games(
        self,
        min_mb: float = DEFAULT_BROWSER_GAME_MIN_MB,
        origin_filter: Optional[str] = None,
        force_browsers: bool = False,
        verbose: bool = False,
    ) -> tuple[int, float]:
        """Clean large downloaded browser games & heavy site storage (>= ``min_mb``).

        Never touches cookies, passwords, bookmarks, history, extensions or LocalStorage.
        Skips currently running browsers unless ``force_browsers=True`` to prevent
        corrupting live LevelDB/SQLite profile databases.
        """
        items = self.scan_browser_game_storage(min_mb=min_mb, origin_filter=origin_filter)
        total_units = 0
        total_mb = 0.0
        skipped_by_browser: dict[str, float] = {}

        for item in items:
            running = self._is_process_running(item.process_name)
            if running and not self.dry_run and not force_browsers:
                skipped_by_browser[item.browser] = (
                    skipped_by_browser.get(item.browser, 0.0) + item.size_mb
                )
                continue

            if verbose:
                status = " [browser running — would skip in live]" if (running and self.dry_run) else ""
                print(
                    f"  {C.DIM}[{item.browser} · {item.profile}] "
                    f"{item.kind}: {item.origin} ({_sz(item.size_mb)}){status}{C.RESET}"
                )

            deleted_any = False
            if item.is_app_support:
                # Delete atomic origin/bucket directory trees together
                for p in item.paths:
                    if self._safe_delete(p, item.size_mb / max(1, len(item.paths))):
                        deleted_any = True
            else:
                # For Cache / Code Cache/wasm, wipe files inside while keeping root container
                for root_p in item.paths:
                    for fpath, fsz in self._scan_files(root_p, min_age_s=0):
                        if self._safe_delete(fpath, fsz):
                            deleted_any = True
                    if deleted_any and not self.dry_run:
                        self._prune_empty_dirs(root_p)

            if deleted_any:
                total_units += 1
                total_mb += item.size_mb
                if not hasattr(self, "_claimed_dirs"):
                    self._claimed_dirs = set()
                for p in item.paths:
                    try:
                        self._claimed_dirs.add(os.path.realpath(p))
                    except OSError:
                        pass

        for browser_name, blocked_mb in skipped_by_browser.items():
            self._record_warning(
                f"{browser_name} is running — close it (or pass --force-browsers) "
                f"to safely clean {_sz(blocked_mb)} of browser game storage."
            )

        return total_units, total_mb

    def clean_dev_caches(self, days: int = DEFAULT_DAYS, verbose: bool = False) -> tuple[int, float]:
        """Clean developer caches not already covered by ``clean_caches``."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        for d in self._dedupe_dirs(DEV_CACHE_DIRS):
            if verbose:
                print(f"  {C.DIM}Cleaning {_shorten_path(d)}{C.RESET}")

            deleted_in_dir = 0
            for path, size in self._scan_files(d, min_age_s=age_s):
                if self._safe_delete(path, size):
                    total_files += 1
                    deleted_in_dir += 1
                    total_mb += size
            if deleted_in_dir > 0 and not self.dry_run:
                self._prune_empty_dirs(d)

        return total_files, total_mb

    def clean_ds_store(self, verbose: bool = False) -> tuple[int, float]:
        """Remove .DS_Store files from home directory."""
        total_files = 0
        total_mb = 0.0

        if not self._is_within_allowed(HOME):
            return total_files, total_mb
        if verbose:
            print(f"  {C.DIM}Scanning ~ for .DS_Store files{C.RESET}")

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
                                size = _stat_size_bytes(entry.stat(follow_symlinks=False)) / MB
                                if self._safe_delete(entry.path, size):
                                    total_files += 1
                                    total_mb += size
                            except (PermissionError, OSError):
                                continue
            except (PermissionError, OSError):
                continue

        return total_files, total_mb

    def clean_logs(self, days: int = MIN_LOG_DAYS, verbose: bool = False) -> tuple[int, float]:
        """Clean diagnostic reports older than ``days``."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        for d in self._dedupe_dirs(LOG_CLEANUP_DIRS):
            if verbose:
                print(f"  {C.DIM}Cleaning {_shorten_path(d)}{C.RESET}")

            for path, size in self._scan_files(d, min_age_s=age_s):
                if self._safe_delete(path, size):
                    total_files += 1
                    total_mb += size

        return total_files, total_mb

    def clean_mail(self, days: int = MIN_MAIL_DAYS, verbose: bool = False) -> tuple[int, float]:
        """Clean old Mail.app attachment copies, leaving recent downloads."""
        if days < 0:
            raise ValueError("days must be non-negative")
        age_s = days * DAY
        total_files = 0
        total_mb = 0.0

        if not os.path.isdir(MAIL_DOWNLOADS):
            return total_files, total_mb

        if verbose:
            print(f"  {C.DIM}Cleaning {_shorten_path(MAIL_DOWNLOADS)}{C.RESET}")

        for path, size in self._scan_files(MAIL_DOWNLOADS, min_age_s=age_s):
            if self._safe_delete(path, size):
                total_files += 1
                total_mb += size

        return total_files, total_mb

    def clean_language_files(self, verbose: bool = False) -> tuple[int, float]:
        """Remove non-English resources only from unsigned user applications."""
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
            print(f"  {C.DIM}Scanning {_shorten_path(search_root)}{C.RESET}")

        try:
            app_entries = list(os.scandir(search_root))
        except PermissionError:
            self._record_permission_denied(search_root)
            return total_files, total_mb
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

    def empty_trash(self, verbose: bool = False) -> tuple[int, float]:
        """Empty the user's Trash."""
        trash = os.path.join(HOME, ".Trash")
        total_files = 0
        total_mb = 0.0

        if not os.path.isdir(trash):
            return total_files, total_mb

        if verbose:
            print(f"  {C.DIM}Emptying {_shorten_path(trash)}{C.RESET}")

        try:
            with os.scandir(trash) as it:
                for entry in it:
                    if entry.is_symlink():
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            size = self._tree_size(entry.path)
                        else:
                            size = _stat_size_bytes(entry.stat(follow_symlinks=False)) / MB
                        if self._safe_delete(entry.path, size):
                            total_files += 1
                            total_mb += size
                    except (PermissionError, OSError):
                        continue
        except PermissionError:
            self._record_permission_denied(trash)
        except OSError:
            pass

        return total_files, total_mb

    def _run_privileged(self, cmd: list[str], shell_script: str, timeout: int = 30) -> bool:
        """Run a command directly if root, otherwise via macOS admin osascript dialog."""
        if os.geteuid() == 0:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            return True
        script = f'do shell script "{shell_script}" with administrator privileges'
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return True

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

        try:
            self._run_privileged(
                ["killall", "-HUP", "mDNSResponder"],
                "killall -HUP mDNSResponder 2>/dev/null",
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

        try:
            self._run_privileged(["purge"], "purge")
            self._log("Memory purged")
            return True
        except (OSError, subprocess.SubprocessError):
            self._log("Memory purge: user cancelled or failed")
            return False

    def run_admin_maintenance(
        self, flush_dns: bool = True, free_ram: bool = True, verbose: bool = False
    ) -> bool:
        """Run privileged DNS flush and/or RAM purge under a single admin prompt."""
        if self.dry_run or (not flush_dns and not free_ram):
            self._log("[DRY] Admin maintenance skipped")
            return False
        if verbose:
            print(f"  {C.DIM}Running administrator maintenance...{C.RESET}")

        parts: list[str] = []
        if flush_dns:
            parts.append("dscacheutil -flushcache")
            parts.append("killall -HUP mDNSResponder 2>/dev/null || true")
        if free_ram:
            parts.append("purge")
        shell_cmd = "; ".join(parts)

        try:
            if os.geteuid() == 0:
                subprocess.run(
                    ["/bin/sh", "-c", shell_cmd],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                )
            else:
                script = f'do shell script "{shell_cmd}" with administrator privileges'
                subprocess.run(
                    ["osascript", "-e", script],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
            self._log(f"Admin maintenance completed (dns={flush_dns}, ram={free_ram})")
            return True
        except (OSError, subprocess.SubprocessError):
            self._log("Admin maintenance: user cancelled or failed")
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

    # ── Analysis & System Info ───────────────────────────────────────────

    def analyze_storage_summary(self) -> list[tuple[str, str, float]]:
        """Measure major cache, browser, updater and developer directories."""
        candidates = [
            ("User Caches", os.path.join(HOME, "Library/Caches")),
            ("Chrome Caches", os.path.join(HOME, "Library/Caches/Google/Chrome")),
            ("Chrome Profile Data", os.path.join(HOME, "Library/Application Support/Google/Chrome")),
            ("Chrome Updater Cache", CHROME_UPDATER_CRX_CACHE),
            ("Xcode DerivedData", os.path.join(HOME, "Library/Developer/Xcode/DerivedData")),
            ("iOS Simulator Caches", os.path.join(HOME, "Library/Developer/CoreSimulator/Caches")),
            ("npm Cache", os.path.join(HOME, ".npm/_cacache")),
            ("Gradle Caches", os.path.join(HOME, ".gradle/caches")),
            ("Bun Cache", os.path.join(HOME, ".bun/install/cache")),
            ("pip Cache", os.path.join(HOME, ".cache/pip")),
            ("uv Cache", os.path.join(HOME, ".cache/uv")),
            ("Trash", os.path.join(HOME, ".Trash")),
        ]
        summary: list[tuple[str, str, float]] = []
        for label, path in candidates:
            if not os.path.isdir(path):
                continue
            size_mb = self._tree_size(path)
            if size_mb >= 1.0:
                summary.append((label, path, size_mb))
        summary.sort(key=lambda item: item[2], reverse=True)
        return summary

    def find_large_files(
        self, min_size_mb: float = 100, limit: int = 50
    ) -> list[tuple[str, float]]:
        """Find the largest files in the home directory using a min-heap."""
        if min_size_mb < 0:
            raise ValueError("min_size_mb must be non-negative")
        if limit <= 0:
            return []
        heap: list[tuple[float, str]] = []

        stack = [HOME]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False):
                            name = entry.name
                            if name in SKIP_DIRS:
                                continue
                            if name.startswith(".") and name not in LARGE_FILE_ALLOWED_DOTDIRS:
                                continue
                            stack.append(entry.path)
                            continue
                        if entry.is_symlink():
                            continue
                        if entry.name.startswith("."):
                            continue
                        try:
                            size = _stat_size_bytes(entry.stat(follow_symlinks=False)) / MB
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

    def _native_memory_stats(self) -> tuple[float, float, float, float]:
        """Return (total_gb, used_gb, free_gb, pct) using sysctl + vm_stat."""
        env = _c_env()
        try:
            total_bytes = int(subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, env=env, timeout=5
            ).strip())
            vm_out = subprocess.check_output(
                ["vm_stat"], text=True, env=env, timeout=5
            )
            page_match = re.search(r"page size of (\d+) bytes", vm_out)
            page_size = int(page_match.group(1)) if page_match else 4096

            pages: dict[str, int] = {}
            for line in vm_out.splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                digits = re.sub(r"[^\d]", "", v)
                if digits:
                    pages[k.strip().strip('"')] = int(digits)

            active = pages.get("Pages active", 0)
            wired = pages.get("Pages wired down", 0)
            compressor = pages.get("Pages occupied by compressor", 0)
            used_bytes = min(total_bytes, (active + wired + compressor) * page_size)
            avail_bytes = max(0, total_bytes - used_bytes)
            pct = (used_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
            return total_bytes / GB, used_bytes / GB, avail_bytes / GB, pct
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0.0, 0.0, 0.0, 0.0

    def _native_cpu_pct(self) -> float:
        """Return approximate aggregate CPU percentage using ps + sysctl."""
        env = _c_env()
        try:
            ncpu = max(1, int(subprocess.check_output(
                ["sysctl", "-n", "hw.ncpu"], text=True, env=env, timeout=5
            ).strip()))
            ps_out = subprocess.check_output(
                ["ps", "-A", "-o", "%cpu="], text=True, env=env, timeout=5
            )
            total = 0.0
            for line in ps_out.splitlines():
                s = line.strip().replace(",", ".")
                if s:
                    try:
                        total += float(s)
                    except ValueError:
                        continue
            return max(0.0, min(100.0, total / ncpu))
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0.0

    def system_info(self) -> dict:
        """Get current system resource usage (uses psutil if installed, else native macOS)."""
        disk = shutil.disk_usage("/")
        if psutil is not None:
            mem = psutil.virtual_memory()
            mem_total_gb = mem.total / GB
            mem_used_gb = mem.used / GB
            mem_free_gb = mem.available / GB
            mem_pct = mem.percent
            cpu_pct = psutil.cpu_percent(interval=0.1)
            backend = "psutil"
        else:
            mem_total_gb, mem_used_gb, mem_free_gb, mem_pct = self._native_memory_stats()
            cpu_pct = self._native_cpu_pct()
            backend = "native"

        return {
            "disk_total_gb": disk.total / GB,
            "disk_used_gb": disk.used / GB,
            "disk_free_gb": disk.free / GB,
            "disk_pct": disk.used / disk.total * 100,
            "mem_total_gb": mem_total_gb,
            "mem_used_gb": mem_used_gb,
            "mem_free_gb": mem_free_gb,
            "mem_pct": mem_pct,
            "cpu_pct": cpu_pct,
            "metrics_backend": backend,
            "last_run": self.stats.get("last_run"),
            "last_cleaned_mb": self.stats.get("last_size", 0.0),
            "last_cleaned_files": self.stats.get("last_files", 0),
            "total_cleaned_mb": self.stats.get("total_size", 0.0),
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


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed



# ── Interactive Terminal UI ───────────────────────────────────────────────────

# ── Layout & Styling ─────────────────────────────────────────────────────────

BOX_W = 74
INNER_W = BOX_W - 4

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _vis_len(s: str) -> int:
    return len(ANSI_RE.sub("", s))


def _pad(s: str, width: int) -> str:
    diff = width - _vis_len(s)
    return s + (" " * max(0, diff))


def _clear_screen() -> None:
    if C._on:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def _setup_terminal() -> None:
    if C._on:
        # Set Terminal window title and a comfortable width so boxes never wrap
        sys.stdout.write("\033]0;Mac Clean — System & Browser Game Cleaner\007")
        sys.stdout.write("\033[8;38;86t")
        sys.stdout.flush()


def _box_top(title: str = "", color: str = C.CYAN) -> str:
    if not title:
        return f"  {color}╭{'─' * (BOX_W - 2)}╮{C.RESET}"
    label = f" {title} "
    rem = max(0, BOX_W - 3 - _vis_len(label))
    return f"  {color}╭─{C.RESET}{C.BOLD}{label}{C.RESET}{color}{'─' * rem}╮{C.RESET}"


def _box_mid(color: str = C.DIM) -> str:
    return f"  {color}├{'─' * (BOX_W - 2)}┤{C.RESET}"


def _box_row(content: str, border_color: str = C.CYAN) -> str:
    return f"  {border_color}│{C.RESET} {_pad(content, INNER_W)} {border_color}│{C.RESET}"


def _box_wrapped_rows(prefix: str, text: str, border_color: str = C.YELLOW) -> list[str]:
    """Wrap plain ``text`` neatly inside a bordered box with ``prefix`` on line 1."""
    prefix_len = _vis_len(prefix)
    avail = max(20, INNER_W - prefix_len)
    lines = textwrap.wrap(text, width=avail) or [""]
    rows = [_box_row(f"{prefix}{lines[0]}", border_color)]
    indent = " " * prefix_len
    for cont in lines[1:]:
        rows.append(_box_row(f"{indent}{cont}", border_color))
    return rows


def _box_bot(color: str = C.CYAN) -> str:
    return f"  {color}╰{'─' * (BOX_W - 2)}╯{C.RESET}"


def _read_key_timeout(timeout_s: float) -> Optional[str]:
    """Read a single keypress within timeout_s seconds (returns None on timeout)."""
    if not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        rlist, _, _ = select.select([fd], [], [], timeout_s)
        if not rlist:
            return None
        ch = os.read(fd, 1).decode("utf-8", errors="ignore")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key() -> str:
    """Wait for a single keypress."""
    if not sys.stdin.isatty():
        return "\n"
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return os.read(fd, 1).decode("utf-8", errors="ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── Pre-Flight Checks ────────────────────────────────────────────────────────

def detect_running_browsers(cleaner: SystemCleanup) -> list[tuple[str, str]]:
    """Return list of (display_name, process_name) for currently running browsers."""
    cleaner._running_processes_cache = None  # Refresh live process snapshot
    running: list[tuple[str, str]] = []
    seen_procs: set[str] = set()
    for display_name, _, _, proc_name in CHROMIUM_BROWSERS:
        if proc_name not in seen_procs and cleaner._is_process_running(proc_name):
            running.append((display_name, proc_name))
            seen_procs.add(proc_name)
    for display_name, proc_name in (("Firefox", "firefox"), ("Safari", "Safari")):
        if proc_name not in seen_procs and cleaner._is_process_running(proc_name):
            running.append((display_name, proc_name))
            seen_procs.add(proc_name)
    return running


def check_chrome_tcc_blocked() -> list[str]:
    """Return protected browser App Support paths blocked by macOS Privacy (TCC)."""
    blocked: list[str] = []
    app_support = os.path.join(HOME, "Library/Application Support")
    for _, app_rel, _, _ in CHROMIUM_BROWSERS:
        p = os.path.join(app_support, app_rel)
        if not os.path.isdir(p):
            continue
        try:
            with os.scandir(p) as it:
                next(it, None)
        except PermissionError:
            blocked.append(p)
        except OSError:
            continue
    return blocked


def quit_browsers_gracefully(browsers: list[tuple[str, str]], cleaner: SystemCleanup) -> list[str]:
    """Ask macOS to quit each running browser cleanly and wait briefly for exit."""
    still_running: list[str] = []
    for display_name, proc_name in browsers:
        try:
            subprocess.run(
                ["osascript", "-e", f'tell application "{proc_name}" to quit'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    # Wait up to 6 seconds for browsers to finish flushing profile DBs and exit
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        cleaner._running_processes_cache = None
        remaining = [name for name, proc in browsers if cleaner._is_process_running(proc)]
        if not remaining:
            return []
        time.sleep(0.4)

    cleaner._running_processes_cache = None
    for display_name, proc_name in browsers:
        if cleaner._is_process_running(proc_name):
            still_running.append(display_name)
    return still_running


def open_full_disk_access_settings() -> None:
    """Open macOS System Settings directly to Privacy & Security -> Full Disk Access."""
    try:
        subprocess.run(
            [
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def ask_macos_popup(
    title: str,
    message: str,
    confirm_label: str = "Allow",
    cancel_label: str = "Skip",
    timeout_s: int = 45,
) -> bool:
    """Show a native macOS dialog popup asking the user to approve an action.

    Uses AppleScript ``argv`` parameters to avoid string-escaping issues.
    Returns True only when the user clicks ``confirm_label``.
    """
    script = (
        "on run argv\n"
        "  set dlgTitle to item 1 of argv\n"
        "  set dlgMsg to item 2 of argv\n"
        "  set cancelBtn to item 3 of argv\n"
        "  set okBtn to item 4 of argv\n"
        "  set res to display dialog dlgMsg with title dlgTitle "
        "buttons {cancelBtn, okBtn} default button okBtn cancel button cancelBtn with icon note\n"
        "  return button returned of res\n"
        "end run"
    )
    try:
        out = subprocess.check_output(
            ["osascript", "-e", script, "--", title, message, cancel_label, confirm_label],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
        return out.strip() == confirm_label
    except (OSError, subprocess.SubprocessError):
        return False


# ── UI Rendering ─────────────────────────────────────────────────────────────

def render_header(info: dict, subtitle: str) -> None:
    print()
    print(_box_top(f"MAC CLEAN  v{VERSION}", C.CYAN))
    print(_box_row(f"{C.BOLD}{subtitle}{C.RESET}"))
    print(_box_mid(C.CYAN))
    disk_line = (
        f"{C.BOLD}DISK  {C.RESET} {_bar(info['disk_pct'], 22)}  "
        f"{info['disk_used_gb']:.1f}/{info['disk_total_gb']:.1f} GB  "
        f"{C.GREEN}{info['disk_free_gb']:.1f} GB free{C.RESET}"
    )
    mem_line = (
        f"{C.BOLD}MEMORY{C.RESET} {_bar(info['mem_pct'], 22)}  "
        f"{info['mem_used_gb']:.1f}/{info['mem_total_gb']:.1f} GB  "
        f"{C.GREEN}{info['mem_free_gb']:.1f} GB free{C.RESET}"
    )
    cpu_line = (
        f"{C.BOLD}CPU   {C.RESET} {_bar(info['cpu_pct'], 12)}  "
        f"{info['cpu_pct']:.0f}%"
    )
    print(_box_row(disk_line))
    print(_box_row(mem_line))
    print(_box_row(cpu_line))
    if info.get("last_run"):
        ts = time.strftime("%b %d %H:%M", time.localtime(info["last_run"]))
        hist = (
            f"{C.DIM}Lifetime cleaned: {info['total_cleaned_files']} items · "
            f"{_sz(info['total_cleaned_mb'])}  (last run {ts}){C.RESET}"
        )
        print(_box_row(hist))
    print(_box_mid(C.CYAN))
    print(_box_row(
        f"{C.GREEN}✓ SAFE{C.RESET} {C.DIM}Protects Documents, Downloads, Apps, Logins & Bookmarks{C.RESET}"
    ))
    print(_box_bot(C.CYAN))
    print()


def run_preflight(cleaner: SystemCleanup, interactive: bool) -> None:
    """Display pre-flight browser & TCC status, helping unlock full browser-game cleanup."""
    running = detect_running_browsers(cleaner)
    blocked = check_chrome_tcc_blocked()

    if not running and not blocked:
        print(f"  {C.GREEN}✓{C.RESET} Pre-flight ready — browsers closed & storage accessible.\n")
        return

    print(_box_top("PRE-FLIGHT CHECK", C.YELLOW))
    if running:
        names = ", ".join(n for n, _ in running)
        print(_box_row(
            f"{C.YELLOW}!{C.RESET} Open browsers: {C.BOLD}{names}{C.RESET}",
            C.YELLOW,
        ))
        print(_box_row(
            f"  {C.DIM}Closing browsers lets Mac Clean safely remove 6 GB+ game data.{C.RESET}",
            C.YELLOW,
        ))
    if blocked:
        for p in blocked[:3]:
            print(_box_row(
                f"{C.YELLOW}!{C.RESET} macOS Privacy blocks: {C.DIM}{_shorten_path(p)}{C.RESET}",
                C.YELLOW,
            ))
        print(_box_row(
            f"  {C.DIM}Grant Full Disk Access to Terminal to unlock Chrome game profiles.{C.RESET}",
            C.YELLOW,
        ))
    print(_box_bot(C.YELLOW))
    print()

    if not interactive:
        return

    if blocked:
        print(f"  {C.YELLOW}→{C.RESET} Asking via macOS popup to grant Full Disk Access...")
        approved_fda = ask_macos_popup(
            title="Mac Clean — Permission Needed",
            message=(
                "macOS Privacy is blocking Chrome's profile storage "
                "(where 6 GB+ browser games are stored).\n\n"
                "Open System Settings → Full Disk Access so you can enable Terminal?"
            ),
            confirm_label="Open Settings",
            cancel_label="Skip for Now",
        )
        if approved_fda:
            open_full_disk_access_settings()
            print(
                f"  {C.CYAN}ℹ{C.RESET} Enable {C.BOLD}Terminal{C.RESET} in Full Disk Access, then re-launch "
                f"Mac Clean. Continuing with accessible caches now...\n"
            )
            time.sleep(1.0)
        else:
            print(f"  {C.DIM}Skipped Full Disk Access — continuing with accessible caches.{C.RESET}\n")

    if running:
        names = ", ".join(n for n, _ in running)
        print(f"  {C.CYAN}→{C.RESET} Asking via macOS popup to close {C.BOLD}{names}{C.RESET}...")
        approved_quit = ask_macos_popup(
            title="Mac Clean — Close Running Browsers",
            message=(
                f"{names} is currently open.\n\n"
                "Close it cleanly now so Mac Clean can safely remove browser games "
                "and caches? (Your passwords, bookmarks and cookies stay safe.)"
            ),
            confirm_label="Close Browsers",
            cancel_label="Keep Open",
        )
        if approved_quit:
            print(f"  {C.DIM}Closing browsers cleanly...{C.RESET}")
            still = quit_browsers_gracefully(running, cleaner)
            if still:
                print(
                    f"  {C.YELLOW}!{C.RESET} Still open: {', '.join(still)} "
                    f"{C.DIM}(will skip their active profile databases){C.RESET}\n"
                )
            else:
                print(f"  {C.GREEN}✓{C.RESET} All browsers closed cleanly.\n")
        else:
            print(f"  {C.DIM}Kept browsers open (active profile storage will be skipped safely).{C.RESET}\n")


def choose_mode_or_auto(interactive: bool) -> str:
    """Auto-start full safe cleanup after 3s unless user presses M for the mode menu."""
    if not interactive:
        return "auto"

    print(
        f"  {C.CYAN}●{C.RESET} Starting {C.BOLD}Automatic Efficiency Clean{C.RESET} "
        f"{C.DIM}(Browser Games + All Browser Caches + System & Dev){C.RESET}"
    )
    for sec in (3, 2, 1):
        sys.stdout.write(
            f"\r  {C.DIM}Starting in {sec}s — press [Enter] now or [M] for options menu... {C.RESET}"
        )
        sys.stdout.flush()
        ch = _read_key_timeout(1.0)
        if ch is not None:
            print("\r" + " " * 78 + "\r", end="", flush=True)
            if ch.lower() == "m":
                return show_mode_menu()
            if ch.lower() == "q":
                return "quit"
            return "auto"

    print("\r" + " " * 78 + "\r", end="", flush=True)
    return "auto"


def show_mode_menu() -> str:
    print(_box_top("CHOOSE CLEANUP MODE", C.CYAN))
    print(_box_row(f"{C.BOLD}[1]{C.RESET} {C.GREEN}Automatic Clean (Recommended){C.RESET}  {C.DIM}Browser games, caches, dev, logs, QuickLook{C.RESET}"))
    print(_box_row(f"{C.BOLD}[2]{C.RESET} {C.YELLOW}Deep Clean{C.RESET}                     {C.DIM}Everything above + Empty Trash, DNS & RAM{C.RESET}"))
    print(_box_row(f"{C.BOLD}[3]{C.RESET} {C.CYAN}Browser Games & Caches Only{C.RESET}    {C.DIM}6 GB+ Chrome/Firefox games & all caches{C.RESET}"))
    print(_box_row(f"{C.BOLD}[4]{C.RESET} {C.BLUE}Analyze Storage (Read-Only){C.RESET}    {C.DIM}Inspect browser games, caches & large files{C.RESET}"))
    print(_box_row(f"{C.BOLD}[5]{C.RESET} {C.DIM}Dry Run Preview{C.RESET}                {C.DIM}Simulate full clean without deleting files{C.RESET}"))
    print(_box_row(f"{C.BOLD}[Q]{C.RESET} {C.DIM}Quit{C.RESET}"))
    print(_box_bot(C.CYAN))
    print(f"\n  Select {C.BOLD}[1-5 / Q]{C.RESET}: ", end="", flush=True)
    ch = _read_key().lower()
    print(ch if ch.isprintable() else "")
    print()
    return {
        "1": "auto",
        "\r": "auto",
        "\n": "auto",
        "2": "deep",
        "3": "browsers",
        "4": "analyze",
        "5": "dry_run",
        "q": "quit",
    }.get(ch, "auto")


# ── Execution Flows ──────────────────────────────────────────────────────────

def _local_dns_flush(cleaner: SystemCleanup) -> bool:
    """Flush local macOS DNS cache without triggering a sudo password dialog."""
    if cleaner.dry_run:
        return False
    try:
        subprocess.run(
            ["dscacheutil", "-flushcache"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        cleaner._log("Local DNS cache flushed (dscacheutil)")
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _admin_maintenance_with_popup(cleaner: SystemCleanup, interactive: bool) -> bool:
    """Ask via macOS popup before requesting admin Touch ID/password for DNS + RAM purge."""
    if cleaner.dry_run:
        return False
    if os.geteuid() == 0:
        return cleaner.run_admin_maintenance(flush_dns=True, free_ram=True)
    if not interactive:
        return _local_dns_flush(cleaner)

    granted = ask_macos_popup(
        title="Mac Clean — Administrator Permission",
        message=(
            "Mac Clean needs administrator permission to:\n"
            "• Flush the system DNS cache (mDNSResponder)\n"
            "• Purge inactive RAM to speed up your Mac\n\n"
            "Do you want to grant administrator permission?"
        ),
        confirm_label="Grant Permission",
        cancel_label="Skip",
    )
    if not granted:
        # Fall back to non-privileged local DNS cache flush
        _local_dns_flush(cleaner)
        cleaner._record_warning(
            "Skipped administrator DNS reset & RAM purge (permission not granted)."
        )
        return False

    ok = cleaner.run_admin_maintenance(flush_dns=True, free_ram=True)
    if not ok:
        _local_dns_flush(cleaner)
        cleaner._record_warning(
            "Administrator prompt was cancelled — ran local DNS flush only."
        )
    return ok


def run_analyze_ui(cleaner: SystemCleanup) -> None:
    print(f"  {C.CYAN}●{C.RESET} Scanning browser games, cache directories, and large files...\n")
    games = cleaner.scan_browser_game_storage(min_mb=20.0)
    dirs = cleaner.analyze_storage_summary()
    large = cleaner.find_large_files(min_size_mb=100, limit=15)

    print(_box_top("BROWSER GAMES & HEAVY WEB STORAGE (≥ 20 MB)", C.CYAN))
    if not games:
        print(_box_row(f"{C.DIM}None found (check Privacy/TCC notice below if Chrome was blocked).{C.RESET}"))
    else:
        for item in games[:12]:
            sz_col = C.RED if item.size_mb >= 1024 else C.YELLOW if item.size_mb >= 250 else ""
            origin_txt = item.origin[:30]
            line = (
                f"{sz_col}{_sz(item.size_mb):>9}{C.RESET}  "
                f"{item.browser:<13} {C.DIM}{item.kind:<15}{C.RESET} {origin_txt}"
            )
            print(_box_row(line))
    print(_box_bot(C.CYAN))
    print()

    print(_box_top("MAJOR CACHE & DEVELOPER DIRECTORIES", C.CYAN))
    if not dirs:
        print(_box_row(f"{C.DIM}None found.{C.RESET}"))
    else:
        for label, path, sz in dirs[:10]:
            sz_col = C.RED if sz >= 1024 else C.YELLOW if sz >= 250 else ""
            short = _shorten_path(path)
            if len(short) > 34:
                short = "…" + short[-33:]
            print(_box_row(f"{sz_col}{_sz(sz):>9}{C.RESET}  {label:<22} {C.DIM}{short}{C.RESET}"))
    print(_box_bot(C.CYAN))
    print()

    if large:
        print(_box_top("LARGEST INDIVIDUAL FILES (≥ 100 MB)", C.CYAN))
        for path, sz in large[:10]:
            sz_col = C.RED if sz >= 1024 else C.YELLOW if sz >= 500 else ""
            short = _shorten_path(path)
            if len(short) > 56:
                short = "…" + short[-55:]
            print(_box_row(f"{sz_col}{_sz(sz):>9}{C.RESET}  {C.DIM}{short}{C.RESET}"))
        print(_box_bot(C.CYAN))
        print()


def run_cleanup_mode(mode: str, interactive: bool) -> SystemCleanup:
    dry_run = mode == "dry_run"
    is_deep = mode == "deep"
    is_browsers_only = mode == "browsers"

    cleaner = SystemCleanup(dry_run=dry_run, write_state=not dry_run)
    info_before = cleaner.system_info()
    started = time.monotonic()

    # Show discovered heavy browser games upfront so user sees exact sites being cleaned
    preview_games = cleaner.scan_browser_game_storage(min_mb=50.0)
    if preview_games:
        print(_box_top("BROWSER GAMES & HEAVY STORAGE DETECTED (≥ 50 MB)", C.BLUE))
        for item in preview_games[:8]:
            sz_col = C.RED if item.size_mb >= 1024 else C.YELLOW if item.size_mb >= 250 else ""
            running_tag = (
                f" {C.YELLOW}[open]{C.RESET}" if cleaner._is_process_running(item.process_name) else ""
            )
            line = (
                f"{sz_col}{_sz(item.size_mb):>9}{C.RESET}  "
                f"{item.browser:<13} {C.DIM}{item.kind:<15}{C.RESET} "
                f"{item.origin[:24]}{running_tag}"
            )
            print(_box_row(line, C.BLUE))
        if len(preview_games) > 8:
            print(_box_row(f"{C.DIM}… and {len(preview_games) - 8} more items{C.RESET}", C.BLUE))
        print(_box_bot(C.BLUE))
        print()

    cache_excludes = cleaner._browser_cache_roots_for_exclusion()

    ops: list[tuple[str, object]] = [
        (
            "Browser games & web storage (≥ 50 MB)",
            lambda: cleaner.clean_browser_games(min_mb=50.0, force_browsers=False),
        ),
        (
            "Browser caches (all profiles + updater)",
            lambda: cleaner.clean_browser_caches(days=0, force_browsers=False),
        ),
    ]

    if not is_browsers_only:
        ops.extend([
            (
                "System & app caches (> 7 days)",
                lambda: cleaner.clean_caches(
                    days=7,
                    include_temp=is_deep,
                    include_optional=is_deep,
                    exclude_dirs=cache_excludes,
                ),
            ),
            (
                "Developer caches (Xcode, npm, pip, uv…)",
                lambda: cleaner.clean_dev_caches(days=7),
            ),
            (
                ".DS_Store Finder clutter",
                lambda: cleaner.clean_ds_store(),
            ),
            (
                "Old crash reports (> 30 days)",
                lambda: cleaner.clean_logs(days=30),
            ),
            (
                "Old Mail attachments (> 30 days)",
                lambda: cleaner.clean_mail(days=30),
            ),
            (
                "QuickLook thumbnail cache reset",
                lambda: cleaner.reset_quicklook(),
            ),
        ])
        if is_deep:
            ops.append(("Empty Trash", lambda: cleaner.empty_trash()))
        ops.append((
            "System DNS flush & RAM purge (admin)",
            lambda: _admin_maintenance_with_popup(cleaner, interactive=interactive),
        ))

    title = "DRY RUN SIMULATION" if dry_run else (
        "DEEP CLEAN PROGRESS" if is_deep else (
            "BROWSER CLEANUP PROGRESS" if is_browsers_only else "AUTOMATIC CLEANUP PROGRESS"
        )
    )
    print(_box_top(title, C.CYAN))

    results: list[tuple[str, int, float, float]] = []
    for label, fn in ops:
        if C._on:
            sys.stdout.write(f"  {C.CYAN}│{C.RESET} {C.CYAN}◌{C.RESET} {_pad(label + '…', INNER_W - 2)} {C.CYAN}│{C.RESET}\r")
            sys.stdout.flush()
        t0 = time.monotonic()
        try:
            ret = fn()
        except Exception as exc:  # noqa: BLE001
            cleaner._log(f"Launcher op failed ({label}): {type(exc).__name__}: {exc}")
            ret = (0, 0.0)
        elapsed = time.monotonic() - t0

        if isinstance(ret, tuple):
            items_cnt, freed_mb = ret
        else:
            items_cnt, freed_mb = (1 if ret else 0), 0.0

        results.append((label, items_cnt, freed_mb, elapsed))
        icon = f"{C.GREEN}✓{C.RESET}" if items_cnt > 0 else f"{C.DIM}⊘{C.RESET}"
        name_col = "" if items_cnt > 0 else C.DIM
        freed_str = _sz(freed_mb)
        row_txt = (
            f"{icon} {name_col}{label:<41}{C.RESET} "
            f"{items_cnt:>6}  {C.GREEN if freed_mb >= 1 else C.DIM}{freed_str:>10}{C.RESET}  "
            f"{C.DIM}{elapsed:>4.1f}s{C.RESET}"
        )
        print(_box_row(row_txt))

    total_items = sum(r[1] for r in results)
    total_mb = sum(r[2] for r in results)
    total_elapsed = time.monotonic() - started
    info_after = cleaner.system_info()
    disk_delta_mb = max(0.0, (info_after["disk_free_gb"] - info_before["disk_free_gb"]) * 1024)

    if not dry_run:
        cleaner.stats["last_files"] = total_items
        cleaner.stats["last_size"] = total_mb
        cleaner.stats["total_files"] = int(cleaner.stats.get("total_files", 0)) + total_items
        cleaner.stats["total_size"] = float(cleaner.stats.get("total_size", 0.0)) + total_mb
        cleaner._save_config()

    print(_box_mid(C.CYAN))
    summary_label = "WOULD FREE" if dry_run else "TOTAL FREED"
    total_row = (
        f"  {C.BOLD}{C.GREEN}{summary_label:<41}{C.RESET} "
        f"{C.BOLD}{total_items:>6}  {C.GREEN}{_sz(total_mb):>10}{C.RESET}  "
        f"{C.DIM}{total_elapsed:>4.1f}s{C.RESET}"
    )
    print(_box_row(total_row))
    if not dry_run:
        delta_note = f" (+{_sz(disk_delta_mb)})" if disk_delta_mb >= 1.0 else ""
        disk_row = (
            f"  {C.BOLD}Disk Free:{C.RESET} {info_before['disk_free_gb']:.2f} GB → "
            f"{C.GREEN}{C.BOLD}{info_after['disk_free_gb']:.2f} GB{C.RESET}{C.GREEN}{delta_note}{C.RESET}"
        )
        print(_box_row(disk_row))
    print(_box_bot(C.CYAN))
    print()

    if cleaner.warnings or cleaner.permission_denied:
        print(_box_top("NOTICES", C.YELLOW))
        for w in cleaner.warnings:
            for row in _box_wrapped_rows(f"{C.YELLOW}!{C.RESET} ", w, C.YELLOW):
                print(row)
        for p in cleaner.permission_denied[:5]:
            for row in _box_wrapped_rows(
                f"{C.YELLOW}!{C.RESET} ", f"Privacy blocked: {_shorten_path(p)}", C.YELLOW
            ):
                print(row)
        if cleaner.permission_denied:
            print(_box_row(
                f"  {C.DIM}Press [F] below to open Full Disk Access settings for Terminal.{C.RESET}",
                C.YELLOW,
            ))
        print(_box_bot(C.YELLOW))
        print()

    return cleaner



def run_interactive_ui(mode: Optional[str] = None, interactive: bool = True) -> None:
    """Run the interactive macOS Terminal UI loop."""
    if interactive:
        _setup_terminal()

    next_mode = mode
    while True:
        if interactive:
            _clear_screen()

        probe = SystemCleanup(dry_run=True, write_state=False)
        info = probe.system_info()
        render_header(info, "Automatic macOS System & Browser-Game Cleaner")

        selected = next_mode
        if selected is None:
            run_preflight(probe, interactive=interactive)
            selected = choose_mode_or_auto(interactive=interactive)

        if selected == "quit":
            print(f"  {C.DIM}Goodbye!{C.RESET}\n")
            return

        if selected == "analyze":
            run_analyze_ui(probe)
            cleaner = probe
        else:
            cleaner = run_cleanup_mode(selected, interactive=interactive)

        if not interactive:
            return

        # Post-run footer menu so the Terminal window stays open to review results
        fda_opt = f"  {C.BOLD}[F]{C.RESET} Full Disk Access" if cleaner.permission_denied else ""
        print(
            f"  {C.DIM}Done!{C.RESET}  "
            f"{C.BOLD}[D]{C.RESET} Deep Clean (+Trash/RAM)  "
            f"{C.BOLD}[A]{C.RESET} Analyze{fda_opt}  "
            f"{C.BOLD}[Q/Enter]{C.RESET} Close"
        )
        print("  → ", end="", flush=True)
        ch = _read_key().lower()
        print()
        next_mode = None
        if ch == "d":
            next_mode = "deep"
            continue
        if ch == "a":
            next_mode = "analyze"
            continue
        if ch == "f":
            open_full_disk_access_settings()
            time.sleep(0.5)
            continue
        return


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser separately so validation can be tested."""
    parser = argparse.ArgumentParser(
        description="Lean, zero-dependency macOS system & browser-game cleaner"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview without deletion, admin prompts or state writes",
    )
    parser.add_argument(
        "--days", type=_non_negative_int, default=DEFAULT_DAYS,
        help=f"Cache age threshold in days (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--browser-days", type=_non_negative_int, default=None,
        help="Age threshold in days for browser caches (defaults to --days)",
    )
    parser.add_argument(
        "--browser-min-mb", type=_non_negative_float, default=DEFAULT_BROWSER_GAME_MIN_MB,
        help=(
            "Minimum size in MB for browser game / heavy site storage cleanup "
            f"(default: {DEFAULT_BROWSER_GAME_MIN_MB})"
        ),
    )
    parser.add_argument(
        "--browser-origin", type=str, default=None, metavar="SUBSTRING",
        help="Limit browser game storage cleanup to origins matching SUBSTRING",
    )
    parser.add_argument(
        "--force-browsers", action="store_true",
        help="Allow cleaning active profile storage even while the browser is running",
    )
    parser.add_argument(
        "--aggressive", action="store_true",
        help="Clean non-English resources in unsigned user apps",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Include Photos/temp caches plus Trash, DNS and QuickLook maintenance",
    )
    parser.add_argument(
        "--purge-memory", action="store_true",
        help="Explicitly purge inactive memory with an admin prompt",
    )
    parser.add_argument(
        "--only", nargs="+", default=None, metavar="OP", choices=sorted(OP_KEYS),
        help="Run only the selected operations",
    )
    parser.add_argument(
        "--skip", nargs="+", default=[], metavar="OP", choices=sorted(OP_KEYS),
        help="Skip selected operations",
    )
    parser.add_argument(
        "--analyze", action="store_true",
        help="Inspect browser game storage, cache sizes and large files (no cleanup)",
    )
    parser.add_argument("--info", action="store_true", help="Show system info only")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Detailed progress")
    parser.add_argument(
        "--cli", action="store_true",
        help="Run direct CLI mode instead of the interactive Terminal UI",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "deep", "browsers", "analyze", "dry_run"),
        default=None,
        help="Launch the interactive UI directly in a specific mode",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run UI mode without waiting for keyboard input",
    )
    return parser


def _print_diagnostics(cleaner: SystemCleanup) -> None:
    if cleaner.warnings:
        print(f"\n  {C.YELLOW}{C.BOLD}Notices{C.RESET}")
        for w in cleaner.warnings:
            print(f"  {C.WARN} {w}")
    if cleaner.permission_denied:
        print(f"\n  {C.YELLOW}{C.BOLD}macOS Privacy (TCC) Blocked Paths{C.RESET}")
        for p in cleaner.permission_denied[:8]:
            print(f"  {C.WARN} {_shorten_path(p)}")
        if len(cleaner.permission_denied) > 8:
            print(f"  {C.DIM}  … and {len(cleaner.permission_denied) - 8} more{C.RESET}")
        print(
            f"  {C.DIM}Tip: Grant Full Disk Access to your terminal app in\n"
            f"  System Settings → Privacy & Security → Full Disk Access\n"
            f"  to inspect and clean protected Chrome profile game data.{C.RESET}"
        )


def run_cli(args: argparse.Namespace) -> None:
    started_at = time.monotonic()

    only = set(args.only) if args.only is not None else None
    skip = set(args.skip)
    cleaner = SystemCleanup(
        dry_run=args.dry_run,
        write_state=not (args.dry_run or args.info or args.analyze),
    )

    info_before = cleaner.system_info()

    if args.info:
        if args.json:
            print(json.dumps({"mode": "info", "system": info_before}, indent=2))
            return
        print(f"\n  {C.BOLD}Mac Clean{C.RESET} {C.DIM}v{VERSION}{C.RESET}  {C.DIM}·{C.RESET}  {C.CYAN}READ ONLY{C.RESET}\n")
        _print_system_banner(info_before)
        return

    # ── Analyze mode ─────────────────────────────────────────────────────
    if args.analyze:
        game_items = cleaner.scan_browser_game_storage(
            min_mb=min(args.browser_min_mb, 20.0) if args.browser_min_mb == DEFAULT_BROWSER_GAME_MIN_MB else args.browser_min_mb,
            origin_filter=args.browser_origin,
        )
        storage_summary = cleaner.analyze_storage_summary()
        large = cleaner.find_large_files(min_size_mb=100, limit=20)

        if args.json:
            payload = {
                "mode": "analyze",
                "system": info_before,
                "browser_game_storage": [g.to_dict() for g in game_items],
                "storage_directories": [
                    {"label": lbl, "path": p, "size_mb": round(sz, 2)}
                    for lbl, p, sz in storage_summary
                ],
                "large_files": [
                    {"path": p, "size_mb": round(sz, 2)} for p, sz in large
                ],
                "permission_denied": cleaner.permission_denied,
            }
            print(json.dumps(payload, indent=2))
            return

        print(f"\n  {C.BOLD}Mac Clean{C.RESET} {C.DIM}v{VERSION}{C.RESET}  {C.DIM}·{C.RESET}  {C.CYAN}ANALYZE{C.RESET}\n")
        _print_system_banner(info_before)

        print(f"  {C.BOLD}Browser Games & Heavy Web Storage{C.RESET}\n")
        if not game_items:
            print(f"  {C.DIM}None found above threshold (check TCC notices below if Chrome was blocked).{C.RESET}")
        else:
            for i, item in enumerate(game_items[:20], 1):
                sc = C.RED if item.size_mb >= 1024 else C.YELLOW if item.size_mb >= 250 else ""
                running_tag = f" {C.YELLOW}[running]{C.RESET}" if cleaner._is_process_running(item.process_name) else ""
                print(
                    f"  {i:>3}  {sc}{_sz(item.size_mb):>10}{C.RESET}  "
                    f"{item.browser} {C.DIM}({item.profile} · {item.kind}){C.RESET}  "
                    f"{item.origin}{running_tag}"
                )
        print()

        print(f"  {C.BOLD}Major Cache & Developer Directories{C.RESET}\n")
        if not storage_summary:
            print(f"  {C.DIM}None found.{C.RESET}")
        else:
            for label, path, sz in storage_summary:
                sc = C.RED if sz >= 1024 else C.YELLOW if sz >= 250 else ""
                print(f"  {sc}{_sz(sz):>10}{C.RESET}  {label:<22} {C.DIM}{_shorten_path(path)}{C.RESET}")
        print()

        print(f"  {C.BOLD}Large Files (≥ 100 MB){C.RESET}\n")
        if not large:
            print(f"  {C.DIM}None found.{C.RESET}")
        else:
            for i, (path, size) in enumerate(large, 1):
                sc = C.RED if size >= 1024 else C.YELLOW if size >= 500 else ""
                print(f"  {i:>3}  {sc}{_sz(size):>10}{C.RESET}  {C.DIM}{_shorten_path(path)}{C.RESET}")

        _print_diagnostics(cleaner)
        print()
        return

    # ── Header (live / dry-run) ──────────────────────────────────────────
    if not args.json:
        mode = f"{C.YELLOW}DRY RUN{C.RESET}" if args.dry_run else f"{C.GREEN}LIVE{C.RESET}"
        print(f"\n  {C.BOLD}Mac Clean{C.RESET} {C.DIM}v{VERSION}{C.RESET}  {C.DIM}·{C.RESET}  {mode}\n")
        _print_system_banner(info_before)

    # Determine whether browsers op will run so caches avoids double-scanning browser caches
    browsers_will_run = (
        ("browsers" not in skip)
        and (only is None or "browsers" in only)
    )
    cache_excludes = cleaner._browser_cache_roots_for_exclusion() if browsers_will_run else None

    # (key, label, method_name, method_kwargs, default_enabled, side_effect)
    ops = [
        (
            "caches", "System & app caches", "clean_caches",
            {
                "days": args.days,
                "include_temp": args.full,
                "include_optional": args.full,
                "exclude_dirs": cache_excludes,
            },
            True, False,
        ),
        (
            "browser_games", f"Browser games (≥ {args.browser_min_mb:g} MB)", "clean_browser_games",
            {
                "min_mb": args.browser_min_mb,
                "origin_filter": args.browser_origin,
                "force_browsers": args.force_browsers,
            },
            True, False,
        ),
        (
            "browsers", "Browser caches", "clean_browser_caches",
            {
                "days": args.days if args.browser_days is None else args.browser_days,
                "force_browsers": args.force_browsers,
            },
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
            args.aggressive, False,
        ),
    ]

    results = []
    for key, label, method, kwargs, default_enabled, side_effect in ops:
        if key in skip:
            continue
        if only is not None:
            if key not in only:
                continue
        elif not default_enabled:
            continue
        if args.dry_run and side_effect:
            if not args.json:
                print(f"  {C.SKIP} {label} (skipped in dry-run)")
            continue

        if not args.json:
            print(f"  {C.DOT} {label}")

        op_start = time.monotonic()
        try:
            ret = getattr(cleaner, method)(verbose=args.verbose and not args.json, **kwargs)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            cleaner._log(f"Operation failed: {label}: {type(exc).__name__}")
            if not args.json:
                print(f"  {C.RED}Failed: {label}{C.RESET}")
            results.append((key, label, 0, 0.0, time.monotonic() - op_start))
            continue

        elapsed = time.monotonic() - op_start
        if isinstance(ret, tuple):
            results.append((key, label, ret[0], ret[1], elapsed))
        else:
            results.append((key, label, 1 if ret else 0, 0.0, elapsed))

    total_f = sum(r[2] for r in results)
    total_mb = sum(r[3] for r in results)
    total_elapsed = time.monotonic() - started_at
    info_after = cleaner.system_info()
    disk_delta_mb = max(0.0, (info_after["disk_free_gb"] - info_before["disk_free_gb"]) * 1024)

    if not args.dry_run:
        cleaner.stats["last_files"] = total_f
        cleaner.stats["last_size"] = total_mb
        cleaner.stats["total_files"] = int(cleaner.stats.get("total_files", 0)) + total_f
        cleaner.stats["total_size"] = float(cleaner.stats.get("total_size", 0.0)) + total_mb
        cleaner._save_config()

    if args.json:
        payload = {
            "mode": "dry_run" if args.dry_run else "live",
            "elapsed_seconds": round(total_elapsed, 2),
            "operations": [
                {
                    "key": k,
                    "label": lbl,
                    "items": cnt,
                    "freed_mb": round(mb, 2),
                    "elapsed_seconds": round(el, 2),
                }
                for k, lbl, cnt, mb, el in results
            ],
            "total_items": total_f,
            "total_freed_mb": round(total_mb, 2),
            "disk_free_before_gb": round(info_before["disk_free_gb"], 2),
            "disk_free_after_gb": round(info_after["disk_free_gb"], 2),
            "warnings": cleaner.warnings,
            "permission_denied": cleaner.permission_denied,
        }
        print(json.dumps(payload, indent=2))
        return

    # ── Results Table ────────────────────────────────────────────────────
    W = 66
    print(f"\n  {C.DIM}{'─' * W}{C.RESET}")
    print(f"  {C.BOLD}{'':>2}{'Operation':<32} {'Items':>7}  {'Freed':>12}  {'Time':>7}{C.RESET}")
    print(f"  {C.DIM}{'─' * W}{C.RESET}")

    for _, name, files, mb, elapsed in results:
        icon = C.OK if files > 0 else C.SKIP
        color = "" if files > 0 else C.DIM
        t_str = f"{elapsed:.1f}s"
        print(f"  {icon} {color}{name:<32} {files:>7}  {_sz(mb):>12}  {C.DIM}{t_str:>7}{C.RESET}")

    print(f"  {C.DIM}{'─' * W}{C.RESET}")
    print(
        f"  {C.BOLD}{C.GREEN}  {'TOTAL':<32} {total_f:>7}  "
        f"{_sz(total_mb):>12}  {total_elapsed:>6.1f}s{C.RESET}"
    )
    print(f"  {C.DIM}{'─' * W}{C.RESET}")

    if not args.dry_run and disk_delta_mb >= 1.0:
        print(
            f"  {C.DIM}Disk free: {info_before['disk_free_gb']:.2f} GB → "
            f"{C.GREEN}{info_after['disk_free_gb']:.2f} GB{C.RESET} "
            f"{C.DIM}(+{_sz(disk_delta_mb)}){C.RESET}"
        )

    _print_diagnostics(cleaner)

    if args.dry_run:
        print(f"\n  {C.YELLOW}Dry run — nothing was deleted.{C.RESET}")
    print()


def _print_system_banner(info: dict) -> None:
    print(
        f"  {C.BOLD}DISK{C.RESET}    {_bar(info['disk_pct'])}  "
        f"{info['disk_used_gb']:.1f}/{info['disk_total_gb']:.1f} GB  "
        f"{C.GREEN}{info['disk_free_gb']:.1f} GB free{C.RESET}"
    )
    print(
        f"  {C.BOLD}MEMORY{C.RESET}  {_bar(info['mem_pct'])}  "
        f"{info['mem_used_gb']:.1f}/{info['mem_total_gb']:.1f} GB  "
        f"{C.GREEN}{info['mem_free_gb']:.1f} GB free{C.RESET}"
    )
    print(f"  {C.BOLD}CPU{C.RESET}     {_bar(info['cpu_pct'], 10)}  {info['cpu_pct']:.0f}%")
    if info.get("last_run"):
        t = time.strftime("%b %d %H:%M", time.localtime(info["last_run"]))
        print(
            f"  {C.DIM}Lifetime: {info['total_cleaned_files']} items · "
            f"{_sz(info['total_cleaned_mb'])} (last run {t}){C.RESET}"
        )
    print()



def _should_run_ui(
    args: argparse.Namespace, raw_args: list[str], stdin_is_tty: bool
) -> bool:
    """Decide whether to launch the interactive Terminal UI or direct CLI."""
    if args.cli:
        return False
    if args.mode is not None or args.non_interactive:
        return True
    if not raw_args:
        return stdin_is_tty
    return False


def main(argv: Optional[list[str]] = None) -> None:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_args)
    stdin_is_tty = sys.stdin.isatty()

    if _should_run_ui(args, raw_args, stdin_is_tty):
        run_interactive_ui(
            mode=args.mode,
            interactive=stdin_is_tty and not args.non_interactive,
        )
        return

    run_cli(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}Cancelled.{C.RESET}\n")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  {C.RED}Error: {type(exc).__name__}: {exc}{C.RESET}\n", file=sys.stderr)
        if sys.stdin.isatty():
            print("  Press any key to close...", end="", flush=True)
            _read_key()
            print()
        sys.exit(1)
