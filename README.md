# MacOS System Cleanup

A powerful macOS system cleanup utility with both CLI and GUI interfaces for freeing disk space and optimizing system performance.

## Table of Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [GUI Mode](#gui-mode)
  - [CLI Mode](#cli-mode)
- [Cleanup Operations](#cleanup-operations)
- [Configuration](#configuration)
- [Safety Features](#safety-features)
- [API Reference](#api-reference)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Dual Interface**: Run as a graphical application or command-line tool
- **Real-time System Monitoring**: Live display of disk, memory, and CPU usage
- **Comprehensive Cleanup**: Remove temp files, caches, .DS_Store files, and more
- **Safety First**: Dry-run mode, path validation, and protected directory handling
- **Parallel Processing**: Multi-threaded scanning for faster performance
- **Persistent Statistics**: Track cleanup history across sessions
- **Age-based Filtering**: Only remove files older than a specified number of days

## Screenshots

*GUI interface displays system information and provides checkboxes for selecting cleanup operations with a progress indicator.*

## Requirements

- **Operating System**: macOS (Darwin)
- **Python**: 3.7 or higher
- **Dependencies**:
  ```
  psutil      # System monitoring (CPU, memory, disk)
  tkinter     # GUI (included with Python on macOS)
  ```

## Installation

1. **Clone or download** the repository:
   ```bash
   git clone https://github.com/yourusername/macos-system-cleanup.git
   cd macos-system-cleanup
   ```

2. **Install dependencies**:
   ```bash
   pip install psutil
   ```

   Note: `tkinter` comes pre-installed with Python on macOS.

3. **Make the script executable** (optional):
   ```bash
   chmod +x system_clean_up.py
   ```

## Usage

### GUI Mode

Launch the graphical interface by running the script without arguments:

```bash
python system_clean_up.py
```

The GUI provides:
- **System Information Panel**: Real-time disk, memory, and CPU usage
- **Cleanup Options**: Checkboxes to select which operations to perform
- **Age Selector**: Choose how old files must be before deletion (1-30 days)
- **Analyze Button**: Scan for large files and potential cleanup targets
- **Clean Now Button**: Execute selected cleanup operations
- **Progress Panel**: Live status updates and progress bar

### CLI Mode

Run with command-line arguments for scripted or automated cleanup:

```bash
# Basic cleanup with defaults (7 days old files)
python system_clean_up.py --days 7

# Preview what would be deleted without actually deleting
python system_clean_up.py --dry-run

# Full cleanup with 14-day threshold
python system_clean_up.py --days 14

# Selective cleanup (skip certain operations)
python system_clean_up.py --no-temp --no-dns

# Minimal cleanup (only temp files and caches)
python system_clean_up.py --no-ds-store --no-dns --no-memory --no-restart-ui
```

#### CLI Options

| Flag | Description |
|------|-------------|
| `--days N` | Remove files older than N days (default: 7) |
| `--dry-run` | Preview without deleting (shows what would be removed) |
| `--no-temp` | Skip temporary files cleanup |
| `--no-ds-store` | Skip .DS_Store files cleanup |
| `--no-dns` | Skip DNS cache flush |
| `--no-memory` | Skip memory purge |
| `--no-system-cache` | Skip system/application cache cleanup |
| `--no-restart-ui` | Skip restarting Dock and Finder |

## Cleanup Operations

| Operation | Description | Default |
|-----------|-------------|---------|
| **Temporary Files** | Clears `~/Library/Caches`, `~/Library/Logs`, `/tmp`, and browser caches | Enabled |
| **Application Caches** | Removes cached data from Chrome, Firefox, Safari, Mail, Photos | Enabled |
| **.DS_Store Files** | Removes macOS folder metadata files throughout home directory | Enabled |
| **Unused Language Files** | Removes non-English `.lproj` bundles from applications | Disabled |
| **Empty Trash** | Permanently deletes items in `~/.Trash` | Enabled |
| **Flush DNS Cache** | Clears DNS resolver cache (requires sudo for full flush) | Disabled |
| **Purge Memory** | Frees inactive memory (requires sudo) | Disabled |
| **Restart Dock & Finder** | Refreshes UI by restarting these services | Disabled |
| **Find Large Files** | Locates files over 100MB for manual review | Analysis only |

### Targeted Directories

**Temporary Files**:
- `~/Library/Caches`
- `~/Library/Logs`
- `~/Downloads`
- `~/Library/Application Support/Google/Chrome/Default/Cache`
- `~/Library/Application Support/Firefox/Profiles`
- `~/Library/Safari/Cache`
- `/tmp`
- `/private/var/folders`

**Application Caches**:
- `~/Library/Caches`
- Chrome, Firefox, Safari browser caches
- Mail and Photos app caches

## Configuration

The application stores configuration and statistics at:

- **Config File**: `~/.system_cleanup_config.json`
- **Log File**: `~/system_cleanup.log`

### Config File Contents

```json
{
  "total_size": 1234.56,
  "total_files": 789,
  "last_run": 1699999999.0
}
```

## Safety Features

The application implements multiple layers of protection:

### Platform Verification
- Only runs on macOS (Darwin); exits with error on other platforms

### Path Protection
- **Symlink Safety**: Skips all symbolic links to prevent traversal attacks
- **Path Containment**: Validates real paths stay within allowed directories
- **Allowed Roots**: Operations restricted to `$HOME`, `/tmp`, `/private/var/folders`

### Protected Locations
The following are never modified:
- `.Trash` directory (until explicitly emptying trash)
- `Keychains` directory
- `Preferences` directory
- `Extensions` directory
- Hidden files (starting with `.`)
- Critical system files (`com.apple.dock.plist`, `com.apple.finder.plist`)

### Age-based Filtering
- Only deletes files older than the specified number of days
- Prevents accidental deletion of recent downloads or work

### Dry-Run Mode
- All deletion methods respect `--dry-run` flag
- Preview exactly what would be removed before committing

### Logging
- All operations logged to `~/system_cleanup.log`
- Includes timestamps, file paths, and sizes
- Logs both successes and failures

## API Reference

### SystemCleanup Class

```python
from system_clean_up import SystemCleanup

# Initialize
cleaner = SystemCleanup(dry_run=False, log_path=None)

# Clear temporary files (returns tuple: files_count, size_mb)
files, size = cleaner.clear_temp_files(
    days_old=7,           # Remove files older than N days
    callback=print,       # Progress callback function
    max_workers=4         # Parallel scanning threads
)

# Clear .DS_Store files
files, size = cleaner.clear_ds_store(callback=print)

# Clear application caches
files, size = cleaner.clear_app_caches(callback=print)

# Remove unused language files
files, size = cleaner.remove_language_files(callback=print)

# Empty trash
files, size = cleaner.clear_trash(callback=print)

# Flush DNS cache (returns bool)
success = cleaner.flush_dns_cache(callback=print)

# Purge memory (returns bool, provides sudo command)
success = cleaner.purge_memory(callback=print)

# Restart Dock and Finder (returns bool)
success = cleaner.restart_dock_and_finder(callback=print)

# Find large files (returns list of tuples)
large_files = cleaner.find_large_files(
    min_size_mb=100,      # Minimum file size
    limit=50,             # Max results to return
    callback=print
)
# Returns: [(file_path, size_mb), ...]

# Get system information
info = cleaner.get_system_info()
# Returns dict with: disk_total, disk_used, disk_free, disk_percent,
#                    memory_total, memory_used, memory_free, memory_percent,
#                    cpu_percent, last_cleanup, space_cleaned, files_cleaned
```

### Callback Function

All cleanup methods accept an optional callback for progress reporting:

```python
def my_callback(message: str):
    print(f"[CLEANUP] {message}")

cleaner.clear_temp_files(callback=my_callback)
```

## Troubleshooting

### Common Issues

**"This script is intended to run on macOS only"**
- The application only runs on macOS. Check your platform with `python -c "import platform; print(platform.system())"`

**Permission Denied Errors**
- Some system caches require elevated privileges
- The app logs warnings for inaccessible files and continues
- For full DNS flush and memory purge, run the provided sudo commands manually

**tkinter Not Found**
- Reinstall Python with tkinter support: `brew install python-tk@3.x`

**psutil Not Found**
- Install with: `pip install psutil`

### Sudo Operations

Some operations require administrator privileges:

```bash
# Flush DNS cache completely
sudo dscacheutil -flushcache
sudo killall -HUP mDNSResponder

# Purge inactive memory
sudo purge
```

### Viewing Logs

Check the log file for detailed operation history:

```bash
cat ~/system_cleanup.log
# Or
tail -f ~/system_cleanup.log  # Follow live updates
```

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Test thoroughly with `--dry-run` first
5. Submit a pull request

### Code Conventions

- Type hints on method parameters
- Docstrings on public methods
- Error handling logs warnings instead of silent failures
- Consistent logging format: `[timestamp] message`
- All cleanup methods return `(files_count, size_mb)` tuple

## License

This project is open source. See LICENSE file for details.
