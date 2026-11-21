# MacOS System Cleanup

A lean, single-file, **zero-dependency** macOS system and browser-game cleaner. Double-click one file in Finder or run it directly from Terminal with CLI flags.

## Features

- **One single executable file (`Mac Clean.command`)** — no extra launchers or helper scripts; double-click in Finder for the interactive Terminal UI, or pass flags for CLI/automation
- **Zero mandatory dependencies** — runs out-of-the-box on macOS Python 3.9+ using native `sysctl`, `vm_stat`, `ps` and `shutil.disk_usage` (automatically uses `psutil` if installed)
- **Browser game & heavy web storage cleaner** — finds and removes multi-GB browser games (Unity WebGL, Unreal, Godot, WebGPU, WASM portals) stored in Chrome/Chromium `IndexedDB`, `WebStorage`/OPFS buckets (with read-only `QuotaManager` domain resolution), `Service Worker/CacheStorage`, `File System`, `blob_storage`, `Code Cache/wasm`, `DawnWebGPUCache`, HTTP Cache, and Firefox per-origin storage — across **all profiles** without touching cookies, passwords, bookmarks or LocalStorage
- **Multi-browser cache cleanup** — covers Google Chrome (Stable/Beta/Canary), Brave, Edge, Arc, Vivaldi, Opera, Chromium, Firefox, Safari, and `GoogleUpdater/crx_cache`
- **Running-browser & macOS Privacy (TCC) safety** — skips active browser profile storage when the browser is open (unless `--force-browsers`) and reports macOS Full Disk Access blocks clearly instead of failing silently
- **Fast scanning & pruning** — uses `os.scandir`, deduplicates overlapping directories, handles APFS sparse files accurately, and prunes empty directories left behind
- **Developer-aware** — cleans Xcode DerivedData, CoreSimulator Caches, npm, bun, pip, uv, yarn, Composer and Gradle caches
- **Automation-friendly** — `--only`, `--skip`, `--dry-run`, `--analyze`, `--info`, and `--json` output modes

## Requirements

- **OS**: macOS (Darwin)
- **Python**: 3.9+ (standard library only; `psutil` is optional)

## Quick Start

### Double-Click in Finder (Recommended)

Double-click **`Mac Clean.command`** to open the interactive Terminal UI:

- Displays live Disk, Memory & CPU gauges plus a clear safety banner (`Documents`, `Downloads`, `Desktop`, `Applications`, Passwords, Cookies, and Bookmarks are never touched).
- **Native macOS permission popups**: Asks before quitting open browsers, opening Full Disk Access settings when Chrome profile storage is blocked by TCC, or requesting administrator approval (a single Touch ID / password prompt) to flush system DNS (`mDNSResponder`) and purge inactive RAM.
- **Automatic Efficiency Clean**: Starts automatically after a 3-second countdown (or press `[M]` for Deep Clean + Trash, Browser-Only, Analyze, or Dry Run), cleaning browser games (`≥ 50 MB`), all browser caches (`0 days` + Chrome Updater CRX cache), system/dev caches, `.DS_Store`, old logs/Mail, QuickLook, DNS, and RAM.

### CLI Usage

Pass any flag to the same **`Mac Clean.command`** file to run directly in CLI mode:

```bash
# Inspect browser games, cache directories, and large files (read-only)
./"Mac Clean.command" --analyze

# Preview cleanup without modifying anything
./"Mac Clean.command" --dry-run -v

# Default CLI cleanup (system/browser caches > 7 days, browser games >= 100 MB, dev caches, old logs/mail)
./"Mac Clean.command" --cli

# Clean ONLY browser games & browser caches
./"Mac Clean.command" --only browser_games browsers

# Clean browser games >= 50 MB or matching a specific site
./"Mac Clean.command" --only browser_games --browser-min-mb 50
./"Mac Clean.command" --only browser_games --browser-origin crazygames

# Full maintenance pass (adds Photos/temp caches, Trash, DNS flush, QuickLook)
./"Mac Clean.command" --full

# Explicit memory purge + aggressive unsigned-app language cleanup
./"Mac Clean.command" --full --purge-memory --aggressive

# Machine-readable JSON output
./"Mac Clean.command" --analyze --json
```

> **macOS Privacy (Full Disk Access) Tip:** On modern macOS, `~/Library/Application Support/Google/Chrome` (where Chrome stores `IndexedDB`, `WebStorage`/OPFS, and `CacheStorage` for 6 GB+ browser games) is protected by macOS TCC. If the tool reports blocked Chrome paths, grant **Full Disk Access** to your terminal app in **System Settings → Privacy & Security → Full Disk Access**, close Chrome, and run again.

### CLI Options

| Flag | Description |
|------|-------------|
| `--cli` | Run direct CLI mode instead of the interactive Terminal UI |
| `--mode MODE` | Launch UI directly in `auto`, `deep`, `browsers`, `analyze`, or `dry_run` mode |
| `--non-interactive` | Run UI mode without waiting for keyboard input |
| `--dry-run` | Preview without deleting, admin prompts or state writes |
| `--days N` | Cache age threshold in days (default: `7`) |
| `--browser-days N` | Age threshold in days for browser caches (defaults to `--days`) |
| `--browser-min-mb N` | Minimum size in MB for browser game / heavy site storage cleanup (default: `100`) |
| `--browser-origin TEXT` | Limit browser game storage cleanup to origins matching `TEXT` |
| `--force-browsers` | Allow cleaning active profile storage even while the browser is running |
| `--only OP [OP ...]` | Run only the specified operations |
| `--skip OP [OP ...]` | Skip selected operations |
| `--full` | Include Photos/temp caches plus Trash, DNS and QuickLook maintenance |
| `--purge-memory` | Explicitly run `purge` with an administrator prompt |
| `--aggressive` | Clean non-English resources only in unsigned apps under `~/Applications` |
| `--analyze` | Inspect browser game storage, cache sizes and large files (no cleanup) |
| `--info` | Show system info only |
| `--json` | Emit structured JSON output |
| `-v, --verbose` | Show detailed scanning progress |

### Operations

| Key | Operation | Default |
|-----|-----------|---------|
| `caches` | System & app caches older than `--days` | ✓ |
| `browser_games` | Heavy browser game & web storage (`>= --browser-min-mb`) | ✓ |
| `browsers` | Browser HTTP/WASM/GPU caches & Chrome Updater CRX cache | ✓ |
| `dev` | Xcode, CoreSimulator, npm, bun, pip, uv, yarn, Composer, Gradle caches | ✓ |
| `ds_store` | `.DS_Store` files throughout home directory | ✓ |
| `logs` | Crash reports older than 30 days | ✓ |
| `mail` | Mail downloads older than 30 days | ✓ |
| `trash` | Empty Trash | `--full` / `--only` |
| `quicklook` | QuickLook thumbnail cache | `--full` / `--only` |
| `dns` | Flush DNS cache | `--full` / `--only` |
| `ram` | Free inactive memory | `--purge-memory` / `--only` |
| `language` | Non-English resources in unsigned `~/Applications/*.app` | `--aggressive` / `--only` |

## Safety

- macOS-only check on startup
- Skips all symbolic links (leaf `os.lstat` check on every delete)
- Path containment — restricted to `$HOME`, `/tmp`, `/private/var/folders`
- Protected directories: `.Trash` (until `--full`), `Keychains`, `Preferences`, `Extensions`, `CloudStorage`, `Mobile Documents`
- Browser profiles: **never** deletes Cookies, Passwords/Login Data, Bookmarks, History, Extensions, or LocalStorage
- Running-browser check prevents corrupting open Chrome/Chromium/Firefox LevelDB & SQLite storage
- `~/Downloads` is **never** cleaned
- `--dry-run` performs zero deletions, admin prompts, system commands, or state writes
- Logs to `~/Library/Logs/SystemCleanup/cleanup.log`

## Verification

```bash
python3 -B -m unittest discover -s tests -v
```

## License

This project is open source. See LICENSE file for details.
