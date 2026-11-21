# MacOS System Cleanup

A lean, single-file, zero-dependency macOS system and browser-game cleaner. Conservative defaults, real cleanup.

## Project Overview

Single-file zero-dependency macOS cleaner:
- `Mac Clean.command` — self-contained executable Python 3.9+ script (`#!/usr/bin/env python3`, `sys.dont_write_bytecode = True`).
  - Double-clicking in Finder (or running with no arguments in a TTY) launches the interactive Terminal UI (live Disk/RAM/CPU gauges, browser quit & TCC pre-flight checks with native macOS popups, automatic efficiency cleanup, and mode menu).
  - Passing CLI flags (`--analyze`, `--dry-run`, `--full`, `--only`, `--json`, `--cli`, etc.) runs the direct CLI interface.

## Architecture

### Core Class: `SystemCleanup`

| Method | Purpose |
|--------|---------|
| `_safe_delete()` | Unified file+dir deletion with dry-run, `lstat` symlink check, path containment & TCC tracking |
| `_scan_files()` | Fast recursive scanner using `os.scandir` with directory exclusion and sparse-file sizing |
| `_dedupe_dirs()` | Resolves and removes duplicate/overlapping directory targets |
| `_prune_empty_dirs()` | Removes empty subdirectories left behind after cache cleanup |
| `_is_safe()` | Path containment check with cached realpath roots |
| `clean_caches()` | System + app caches (excludes browser roots when `browsers` runs) |
| `clean_browser_caches()` | Multi-profile Chromium, Firefox, Safari caches + `GoogleUpdater/crx_cache` |
| `scan_browser_game_storage()` | Discovers heavy browser game storage (`IndexedDB`, `WebStorage`/OPFS via read-only `QuotaManager`, `CacheStorage`, `File System`, `blob_storage`, `Code Cache/wasm`, `DawnWebGPUCache`, HTTP Cache, Firefox `storage/default`) |
| `clean_browser_games()` | Atomically removes heavy browser game storage (`>= min_mb`) while protecting running browsers & sensitive profile data |
| `clean_dev_caches()` | Xcode DerivedData, CoreSimulator Caches, npm, bun, pip, uv, yarn, Composer and Gradle caches |
| `clean_ds_store()` | Remove `.DS_Store` files from home directory |
| `clean_logs()` | Old crash reports and diagnostic logs |
| `clean_mail()` | Old Mail.app downloaded attachment copies |
| `clean_language_files()` | Non-English resources in unsigned user apps (supports dry-run preview) |
| `empty_trash()` | Empty `~/.Trash` |
| `flush_dns()` | Flushes DNS via direct root or `osascript` admin prompt |
| `free_ram()` | Purges memory via direct root or `osascript` admin prompt |
| `run_admin_maintenance()` | Runs DNS flush + RAM purge under a single macOS admin prompt |
| `reset_quicklook()` | Resets QuickLook thumbnail cache |
| `analyze_storage_summary()` | Measures major cache, browser, updater and developer directories |
| `find_large_files()` | Heap-based top-N large file finder (skips iCloud/CloudStorage) |
| `system_info()` | Disk, memory, CPU stats (`psutil` if present, else native macOS fallback) |

### Unified Entry Point (`main()`)

- `_should_run_ui()` routes no-arg TTY launches (Finder double-click) and `--mode` / `--non-interactive` to `run_interactive_ui()`, while CLI flags (`--cli`, `--only`, `--skip`, `--dry-run`, `--analyze`, `--info`, `--json`, etc.) or non-TTY invocations route to `run_cli()`.

## Dependencies

Standard library only (`psutil>=5.9` is optional).

## Safety Features

- macOS-only platform check on init
- Symlink protection — `os.lstat` check on every deletion
- Path containment — `_is_safe()` validates realpath within cached allowed roots
- Never deletes allowed roots themselves (`$HOME`, `/tmp`, `/private/var/folders`)
- Skips protected dirs: `.Trash`, `Keychains`, `Preferences`, `Extensions`, `CloudStorage`, `Mobile Documents`
- Browser protection: never touches Cookies, Passwords, Bookmarks, History, Extensions, or LocalStorage; skips running browsers unless `--force-browsers`
- Surfaces macOS TCC (`PermissionError`) blocked paths with Full Disk Access instructions
- `--dry-run` performs no deletion, admin prompt, system command or state write
- Logging to `~/Library/Logs/SystemCleanup/cleanup.log`

## Code Conventions

- `from __future__ import annotations` for Python 3.9+ compatibility
- `sys.dont_write_bytecode = True` to keep Finder free of `__pycache__/`
- Type hints and docstrings on public methods
- `os.scandir` over `os.walk` for performance
- Always pass `LC_ALL=C` when parsing macOS CLI output (`ps`, `vm_stat`, `sysctl`)
- ANSI colors auto-disabled when not a TTY
