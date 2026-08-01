# MacOS System Cleanup

A lean, efficient macOS system cleaner. CLI-only, conservative defaults, real cleanup.

## Features

- **Fast scanning** — uses `os.scandir` for 2-10x faster I/O than `os.walk`
- **Conservative defaults** — age-filtered cache cleanup, protected directories, no profile/app deletion
- **Developer-aware** — cleans Xcode DerivedData, npm, Composer and Gradle caches without following symlinks
- **Maintenance modes** — optional Trash, DNS and QuickLook operations; memory purge is explicit
- **Dry-run mode** — previews deletions without changing files, logs, configuration or system state
- **Colored output** — usage bars, status icons, auto-disabled in non-TTY

## Requirements

- **OS**: macOS (Darwin)
- **Python**: 3.7+
- **Dependencies**: `psutil`

## Installation

```bash
git clone https://github.com/sobrinN/macos-system-cleanup.git
cd macos-system-cleanup
python3 -m pip install -r requirements.txt
```

## Usage

```bash
# Default cleanup (safe caches > 7 days, developer caches, old logs/mail)
python3 system_clean_up.py

# Preview what would be deleted
python3 system_clean_up.py --dry-run

# Custom age threshold
python3 system_clean_up.py --days 14

# Include Photos/temp caches plus maintenance operations (Trash, DNS and QuickLook)
python3 system_clean_up.py --full

# Purge inactive memory only when explicitly requested
python3 system_clean_up.py --purge-memory

# Full maintenance pass, including the memory purge prompt
python3 system_clean_up.py --full --purge-memory --aggressive

# Include language file cleanup
python3 system_clean_up.py --aggressive

# Skip specific operations
python3 system_clean_up.py --skip dns ram

# Find large files only
python3 system_clean_up.py --analyze

# System info only
python3 system_clean_up.py --info

# Verbose scanning output
python3 system_clean_up.py -v
```

### CLI Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview without deleting, prompts or state writes |
| `--days N` | Cache age threshold in days (default: 7) |
| `--aggressive` | Clean non-English resources only in unsigned apps under `~/Applications` |
| `--full` | Include Photos/temp caches plus Trash, DNS and QuickLook maintenance |
| `--purge-memory` | Explicitly run `purge` with an administrator prompt |
| `--skip OP [OP ...]` | Skip operations (see list below) |
| `--analyze` | Find large files only (no cleanup) |
| `--info` | Show system info only |
| `-v, --verbose` | Show detailed scanning progress |

### Operations

| Key | Operation | Default |
|-----|-----------|---------|
| `caches` | System & app caches older than the age threshold | ✓ |
| `dev` | Xcode, npm, Composer and Gradle caches older than the age threshold | ✓ |
| `ds_store` | `.DS_Store` files throughout home directory | ✓ |
| `logs` | Crash reports older than 30 days | ✓ |
| `mail` | Mail downloads older than 30 days | ✓ |
| `trash` | Empty Trash | `--full` only |
| `quicklook` | QuickLook thumbnail cache | `--full` only |
| `dns` | Flush DNS cache | `--full` only |
| `ram` | Free inactive memory | `--purge-memory` only |
| `language` | Remove non-English resources from unsigned user apps | `--aggressive` only |

## Safety

- Only runs on macOS
- Skips all symbolic links
- Path containment — operations restricted to `$HOME`, `/tmp`, `/private/var/folders`
- Protected directories: `.Trash` (until `--full`), `Keychains`, `Preferences`, `Extensions`
- Hidden files and critical plist files are never touched
- Age-based filtering prevents deleting recent cache, log and Mail files
- `~/Downloads` is **never** cleaned
- `--dry-run` performs no deletion, admin prompt, system command or state write
- All operations logged to `~/Library/Logs/SystemCleanup/cleanup.log`

## Targeted Directories

**System Caches**:
- `~/Library/Caches`
- Browser caches (Chrome, Firefox, Safari)
- App container caches (Mail, Photos, Safari)
- `/tmp`, `/private/var/folders` (only with `--full`, because they can be very large)

The broad `~/Library/Caches` pass also covers regenerable package-manager caches
such as Homebrew, pip, Yarn and CocoaPods without scanning that tree twice.
The Photos container cache and temporary directories are reserved for `--full`
because they can be very large.

**Developer Caches**:
- `~/Library/Developer/Xcode/DerivedData`
- `~/.npm/_cacache`
- `~/.composer/cache`
- `~/.gradle/caches`

**Diagnostics**:
- `~/Library/Logs/DiagnosticReports`
- `~/Library/Logs/CrashReporter`

## Configuration

- **Config**: `~/.system_cleanup_config.json` — tracks last run stats
- **Log**: `~/Library/Logs/SystemCleanup/cleanup.log`

## Verification

Run the standard-library test suite with:

```bash
python3 -m unittest discover -s tests -v
```

## License

This project is open source. See LICENSE file for details.
