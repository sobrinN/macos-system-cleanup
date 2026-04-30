# MacOS System Cleanup

A lean, efficient macOS system cleaner. CLI-only, conservative defaults, real cleanup.

## Project Overview

Single-file Python script (`system_clean_up.py`) — no GUI, no frameworks, just a clean CLI.

## Architecture

### Core Class: `SystemCleanup`

| Method | Purpose |
|--------|---------|
| `_safe_delete()` | Unified file+dir deletion with dry-run, symlink, and path validation |
| `_scan_files()` | Fast recursive scanner using `os.scandir` (2-10x faster than `os.walk`) |
| `_is_safe()` | Path containment check with cached realpath roots |
| `clean_caches()` | System + app caches (unified — no double-scanning) |
| `clean_dev_caches()` | Xcode DerivedData, Homebrew, npm, pip, Yarn, CocoaPods, Gradle |
| `clean_ds_store()` | Remove `.DS_Store` files from home directory |
| `clean_logs()` | Old crash reports and diagnostic logs |
| `clean_mail()` | Mail.app downloaded attachment copies |
| `clean_language_files()` | Non-English `.lproj` bundles (aggressive only) |
| `empty_trash()` | Empty `~/.Trash` |
| `flush_dns()` | Actually flushes DNS via `osascript` admin prompt |
| `free_ram()` | Actually purges memory via `osascript` admin prompt |
| `reset_quicklook()` | Resets QuickLook thumbnail cache |
| `find_large_files()` | Heap-based top-N large file finder |
| `system_info()` | Disk, memory, CPU stats |

### CLI (data-driven operation table)

Operations run via a declarative table — no repetitive if-blocks. Output uses ANSI colors with TTY detection.

## Dependencies

```
psutil      # System monitoring (CPU, memory, disk)
```

Standard library only: `os`, `sys`, `subprocess`, `argparse`, `platform`, `time`, `shutil`, `heapq`, `json`, `pathlib`

## Usage

```bash
# Default conservative cleanup
python3 system_clean_up.py

# Preview what would be deleted
python3 system_clean_up.py --dry-run

# Custom age threshold
python3 system_clean_up.py --days 14

# Include language file cleanup
python3 system_clean_up.py --aggressive

# Skip specific operations
python3 system_clean_up.py --skip dns ram

# Find large files only
python3 system_clean_up.py --analyze

# Show system info only
python3 system_clean_up.py --info

# Detailed scanning output
python3 system_clean_up.py -v
```

## Safety Features

- macOS-only platform check on init
- Symlink protection — skips all symlinks
- Path containment — `_is_safe()` validates realpath within cached allowed roots
- Never deletes allowed roots themselves (`$HOME`, `/tmp`, `/private/var/folders`)
- Skips protected dirs: `.Trash`, `Keychains`, `Preferences`, `Extensions`
- Skips system files: hidden files, critical plist files
- Age-based filtering on cache cleanup (default: 7 days)
- `--dry-run` mode respected by all operations
- Logging to `~/Library/Logs/SystemCleanup/cleanup.log`

## Code Conventions

- Type hints on method parameters
- Docstrings on public methods
- `os.scandir` over `os.walk` for performance
- Data-driven CLI — operations defined in a table, iterated in a loop
- ANSI colors auto-disabled when not a TTY
