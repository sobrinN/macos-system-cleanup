# MacOS System Cleanup

A lean, efficient macOS system cleaner. CLI-only, conservative defaults, real cleanup.

## Features

- **Fast scanning** — uses `os.scandir` for 2-10x faster I/O than `os.walk`
- **Conservative defaults** — age-filtered cache cleanup, protected directories
- **Developer-aware** — cleans Xcode DerivedData, Homebrew, npm, pip, Yarn, Gradle caches
- **Actually works** — DNS flush and RAM purge use real admin prompts, not just "run this command"
- **Dry-run mode** — preview everything before deleting
- **Colored output** — usage bars, status icons, auto-disabled in non-TTY

## Requirements

- **OS**: macOS (Darwin)
- **Python**: 3.7+
- **Dependencies**: `psutil`

## Installation

```bash
git clone https://github.com/yourusername/macos-system-cleanup.git
cd macos-system-cleanup
pip install psutil
```

## Usage

```bash
# Default cleanup (caches > 7 days, dev caches, trash, DNS, RAM, etc.)
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

# System info only
python3 system_clean_up.py --info

# Verbose scanning output
python3 system_clean_up.py -v
```

### CLI Options

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview without deleting |
| `--days N` | Cache age threshold in days (default: 7) |
| `--aggressive` | Include language files cleanup |
| `--skip OP [OP ...]` | Skip operations (see list below) |
| `--analyze` | Find large files only (no cleanup) |
| `--info` | Show system info only |
| `-v, --verbose` | Show detailed scanning progress |

### Operations

| Key | Operation | Default |
|-----|-----------|---------|
| `caches` | System & app caches (browser, mail, photos) | ✓ |
| `dev` | Developer caches (Xcode, Homebrew, npm, pip, Yarn, CocoaPods, Gradle) | ✓ |
| `ds_store` | `.DS_Store` files throughout home directory | ✓ |
| `logs` | Crash reports & diagnostic logs | ✓ |
| `mail` | Mail.app downloaded attachment copies | ✓ |
| `trash` | Empty Trash | ✓ |
| `quicklook` | QuickLook thumbnail cache | ✓ |
| `dns` | Flush DNS cache | ✓ |
| `ram` | Free inactive memory | ✓ |
| `language` | Remove non-English `.lproj` bundles | `--aggressive` only |

## Safety

- Only runs on macOS
- Skips all symbolic links
- Path containment — operations restricted to `$HOME`, `/tmp`, `/private/var/folders`
- Protected directories: `.Trash` (until explicitly emptying), `Keychains`, `Preferences`, `Extensions`
- Hidden files and critical plist files are never touched
- Age-based filtering prevents deleting recent cache files
- `~/Downloads` is **never** cleaned
- `--dry-run` respected by all operations
- All operations logged to `~/Library/Logs/SystemCleanup/cleanup.log`

## Targeted Directories

**System Caches**:
- `~/Library/Caches`
- `~/Library/Logs`
- Browser caches (Chrome, Firefox, Safari)
- App container caches (Mail, Photos, Safari)
- `/tmp`, `/private/var/folders`

**Developer Caches**:
- `~/Library/Developer/Xcode/DerivedData`
- `~/Library/Caches/Homebrew`
- `~/.npm/_cacache`
- `~/Library/Caches/pip`
- `~/Library/Caches/Yarn`
- `~/Library/Caches/CocoaPods`
- `~/.composer/cache`
- `~/.gradle/caches`

**Diagnostics**:
- `~/Library/Logs/DiagnosticReports`
- `~/Library/Logs/CrashReporter`

## Configuration

- **Config**: `~/.system_cleanup_config.json` — tracks last run stats
- **Log**: `~/Library/Logs/SystemCleanup/cleanup.log`

## License

This project is open source. See LICENSE file for details.
