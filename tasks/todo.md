# MacOS System Cleanup — Upgrade Plan

## Completed
- [x] Audited dependencies & eliminated hard `psutil` failure with native macOS `sysctl`/`vm_stat`/`ps` fallback (`LC_ALL=C` locale-safe).
- [x] Built comprehensive browser cache & multi-GB browser-game storage cleaner (`clean_browser_caches`, `scan_browser_game_storage`, `clean_browser_games`) covering Chromium `IndexedDB`, `WebStorage`/OPFS (`QuotaManager` SQLite read-only origin lookup), `Service Worker/CacheStorage`, `File System`, `blob_storage`, `Code Cache/wasm`, `DawnWebGPUCache`, HTTP Cache, `GoogleUpdater/crx_cache`, Firefox `storage/default` & `cache2`, and Safari caches.
- [x] Added safety & efficiency upgrades: running-browser guard (`--force-browsers` override), macOS TCC `PermissionError` diagnostics, directory deduplication & claimed-dir non-overlap, empty directory pruning, sparse-file aware sizing, cumulative lifetime config stats, and language-file dry-run preview.
- [x] Added `--only`, `--browser-min-mb`, `--browser-origin`, `--force-browsers`, `--json`, per-op timing, disk-free delta, and upgraded `--analyze` report.
- [x] Consolidated `system_clean_up.py`, `launcher_ui.py`, and `Mac Clean.command` into a single self-contained executable `Mac Clean.command` (double-click in Finder opens interactive UI; CLI flags run direct CLI mode; `sys.dont_write_bytecode = True` keeps Finder clean of `__pycache__`).
- [x] Updated unit tests (`tests/test_system_clean_up.py`) to load `Mac Clean.command` via `importlib` and verify single-file entry-point routing alongside all engine & UI tests.
- [x] Updated `README.md`, `AGENTS.md`, `CLAUDE.md`, `requirements.txt`, and `tasks/lessons.md`.
