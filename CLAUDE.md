# MacOS System Cleanup

A macOS system cleanup utility with both CLI and GUI interfaces for freeing disk space and optimizing system performance.

## Project Overview

Single-file Python application (`system_clean_up.py`) that provides:
- **CLI mode**: Run with command-line arguments for scripted/automated cleanup
- **GUI mode**: Tkinter-based graphical interface (default when run without arguments)

## Architecture

### Core Classes

- `SystemCleanup`: Core cleanup logic with dry-run support, logging, and config persistence
- `CleanupGUI`: Tkinter GUI that wraps SystemCleanup with threading for responsive UI

### Key Design Patterns

- Callback pattern for progress reporting (`callback=` parameter on all cleanup methods)
- Config persistence via JSON at `~/.system_cleanup_config.json`
- Logging to `~/system_cleanup.log`
- Background threading in GUI to prevent UI freezing
- Parallel scanning with `ThreadPoolExecutor` for I/O-bound operations
- Generator-based file iteration for memory efficiency

### Helper Methods

| Method | Purpose |
|--------|---------|
| `_delete_file()` | Safe file deletion with dry-run and logging |
| `_delete_dir()` | Safe directory deletion with dry-run and logging |
| `_get_dir_size()` | Calculate directory size with symlink/permission safety |
| `_is_safe_path()` | Validate paths stay within allowed directories |
| `_iter_files()` | Generator for memory-efficient file iteration |
| `_scan_temp_dir()` | Parallel-safe directory scanner |

## Dependencies

```
psutil      # System monitoring (CPU, memory, disk)
tkinter     # GUI (included with Python on macOS)
```

Standard library: `os`, `sys`, `subprocess`, `argparse`, `platform`, `time`, `shutil`, `heapq`, `concurrent.futures`, `pathlib`, `threading`, `json`

## Running the Application

```bash
# GUI mode (default)
python system_clean_up.py

# CLI mode with options
python system_clean_up.py --days 7 --dry-run

# CLI flags
--days N          # Remove files older than N days (default: 7)
--dry-run         # Preview without deleting
--no-temp         # Skip temp files cleanup
--no-ds-store     # Skip .DS_Store cleanup
--no-dns          # Skip DNS cache flush
--no-memory       # Skip memory purge
--no-system-cache # Skip system cache cleanup
--no-restart-ui   # Skip restarting Dock/Finder
```

## Cleanup Operations

| Operation | Method | Notes |
|-----------|--------|-------|
| Temp files | `clear_temp_files()` | Parallel scanning, age-filtered |
| .DS_Store | `clear_ds_store()` | Recursive home directory scan |
| App caches | `clear_app_caches()` | Chrome, Firefox, Safari, Mail, Photos |
| Language files | `remove_language_files()` | Keeps only English `.lproj` bundles |
| Trash | `clear_trash()` | Single-pass with size calculation |
| DNS cache | `flush_dns_cache()` | Runs `dscacheutil -flushcache` |
| Memory purge | `purge_memory()` | Requires sudo (provides instructions) |
| Restart UI | `restart_dock_and_finder()` | Kills and restarts Dock/Finder |
| Find large files | `find_large_files()` | Heap-based top-N efficiency |

## Safety Features

- Platform check: Only runs on macOS (Darwin)
- Symlink protection: Skips symlinks to prevent traversal attacks
- Path containment: `_is_safe_path()` validates real paths stay in allowed dirs
- Skips protected directories: `.Trash`, `Keychains`, `Preferences`, `Extensions`
- Skips system files: Files starting with `.`, critical plist files
- Age-based filtering: Only deletes files older than specified days
- Dry-run mode: All deletion methods respect `--dry-run` flag

## Code Conventions

- Type hints on method parameters
- Docstrings on public methods
- Error handling logs warnings instead of silent failures
- Consistent logging format: `[timestamp] message`

## Development Notes

- GUI updates system info every 5 seconds via `root.after()`
- All cleanup methods return `(files_count, size_mb)` tuple
- Stats persist between runs for "last cleanup" display
- Some operations (memory purge, DNS flush) require sudo - app provides terminal commands
- Parallel scanning uses 4 workers by default (configurable via `max_workers`)
- Large file finder uses min-heap for O(n log k) efficiency instead of sorting all results
