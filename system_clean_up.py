#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import platform
import time
import shutil
import heapq
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import json

class SystemCleanup:
    def __init__(self, dry_run: bool = False, log_path: str = None):
        self.user_home = str(Path.home())
        self.config_path = os.path.join(self.user_home, '.system_cleanup_config.json')
        self.stats = {
            'total_size': 0,
            'total_files': 0,
            'last_run': None
        }
        self.dry_run = dry_run
        self.log_path = log_path or os.path.join(self.user_home, 'system_cleanup.log')
        self.load_config()
        self._platform_check()
        self._log('SystemCleanup initialized.')

    def _platform_check(self):
        if platform.system() != 'Darwin':
            raise EnvironmentError('This script is intended to run on macOS only.')

    def _log(self, message: str):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(self.log_path, 'a') as log_file:
                log_file.write(f'[{timestamp}] {message}\n')
        except Exception:
            pass

    def _delete_file(self, file_path: str, file_size: float, callback=None) -> bool:
        """Helper to delete a file, supports dry run, path validation, and logging."""
        # Safety check: ensure path is within allowed directories
        allowed_roots = [self.user_home, '/tmp', '/private/var/folders']
        if not self._is_safe_path(file_path, allowed_roots):
            self._log(f"Refusing to delete file outside allowed paths: {file_path}")
            return False

        # Skip symlinks for safety
        if os.path.islink(file_path):
            self._log(f"Skipping symlink: {file_path}")
            return False

        if self.dry_run:
            self._log(f"[DRY RUN] Would remove: {file_path} ({file_size:.2f} MB)")
            if callback:
                callback(f"[DRY RUN] Would remove: {file_path} ({file_size:.2f} MB)")
            return True
        try:
            os.remove(file_path)
            self._log(f"Removed: {file_path} ({file_size:.2f} MB)")
            if callback:
                callback(f"Removed: {file_path} ({file_size:.2f} MB)")
            return True
        except (PermissionError, OSError) as e:
            self._log(f"Failed to remove {file_path}: {e}")
            if callback:
                callback(f"Failed to remove: {file_path}")
            return False

    def _delete_dir(self, dir_path: str, dir_size: float, callback=None) -> bool:
        """Helper to delete a directory, supports dry run, path validation, and logging."""
        # Safety check: ensure path is within allowed directories
        allowed_roots = [self.user_home, '/tmp', '/private/var/folders']
        if not self._is_safe_path(dir_path, allowed_roots):
            self._log(f"Refusing to delete directory outside allowed paths: {dir_path}")
            return False

        # Skip symlinks for safety
        if os.path.islink(dir_path):
            self._log(f"Skipping symlink directory: {dir_path}")
            return False

        if self.dry_run:
            self._log(f"[DRY RUN] Would remove directory: {dir_path} ({dir_size:.2f} MB)")
            if callback:
                callback(f"[DRY RUN] Would remove directory: {dir_path} ({dir_size:.2f} MB)")
            return True
        try:
            shutil.rmtree(dir_path)
            self._log(f"Removed directory: {dir_path} ({dir_size:.2f} MB)")
            if callback:
                callback(f"Removed directory: {dir_path} ({dir_size:.2f} MB)")
            return True
        except (PermissionError, OSError) as e:
            self._log(f"Failed to remove directory {dir_path}: {e}")
            if callback:
                callback(f"Failed to remove directory: {dir_path}")
            return False

    def _get_dir_size(self, path: str) -> float:
        """Calculate directory size in MB, safely handling permissions and symlinks."""
        total = 0
        try:
            for dp, dn, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dp, f)
                    if not os.path.islink(fp):
                        try:
                            total += os.path.getsize(fp)
                        except (PermissionError, OSError):
                            pass
        except (PermissionError, OSError):
            pass
        return total / (1024 * 1024)  # Convert to MB

    def _is_safe_path(self, path: str, allowed_roots: list) -> bool:
        """Verify path is within allowed directories (prevents symlink attacks)."""
        try:
            real_path = os.path.realpath(path)
            return any(
                real_path.startswith(os.path.realpath(root))
                for root in allowed_roots
            )
        except (PermissionError, OSError):
            return False

    def _iter_files(self, directory: str, skip_patterns: list = None, min_age_seconds: float = None):
        """Generator yielding (path, size_mb, mtime) for files in directory.

        Args:
            directory: Root directory to scan
            skip_patterns: List of substrings to skip in paths
            min_age_seconds: Only yield files older than this (seconds since modification)
        """
        skip_patterns = skip_patterns or []
        now = time.time()

        try:
            for root, dirs, files in os.walk(directory):
                # Skip directories matching patterns
                if any(skip in root for skip in skip_patterns):
                    continue

                for file in files:
                    # Skip hidden files and system files
                    if file.startswith(".") or file in ["com.apple.dock.plist", "com.apple.finder.plist"]:
                        continue

                    file_path = os.path.join(root, file)

                    # Skip symlinks
                    if os.path.islink(file_path):
                        continue

                    try:
                        stats = os.stat(file_path)
                        file_size = stats.st_size / (1024 * 1024)  # MB
                        file_mtime = stats.st_mtime
                        file_age = now - file_mtime

                        # Filter by age if specified
                        if min_age_seconds is not None and file_age < min_age_seconds:
                            continue

                        yield file_path, file_size, file_mtime
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            pass

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    self.stats = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
                
    def save_config(self):
        self.stats['last_run'] = time.time()
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.stats, f)
        except IOError:
            pass

    def get_system_info(self):
        """Get system information for display"""
        info = {}
        
        # Disk usage
        disk = shutil.disk_usage('/')
        info['disk_total'] = disk.total / (1024**3)  # GB
        info['disk_used'] = disk.used / (1024**3)    # GB
        info['disk_free'] = disk.free / (1024**3)    # GB
        info['disk_percent'] = disk.used / disk.total * 100
        
        # Memory usage
        mem = psutil.virtual_memory()
        info['memory_total'] = mem.total / (1024**3)  # GB
        info['memory_used'] = mem.used / (1024**3)    # GB
        info['memory_free'] = mem.available / (1024**3)  # GB
        info['memory_percent'] = mem.percent
        
        # CPU usage
        info['cpu_percent'] = psutil.cpu_percent(interval=0.5)
        
        # Last cleanup stats
        info['last_cleanup'] = self.stats.get('last_run', None)
        info['space_cleaned'] = self.stats.get('total_size', 0)
        info['files_cleaned'] = self.stats.get('total_files', 0)
        
        return info

    def _scan_temp_dir(self, temp_dir: str, age_limit_seconds: float) -> list:
        """Scan a single temp directory for old files. Returns list of (path, size) tuples."""
        skip_patterns = [".Trash", "Keychains", "Preferences", "Extensions"]
        results = []

        for file_path, file_size, _ in self._iter_files(
            temp_dir, skip_patterns=skip_patterns, min_age_seconds=age_limit_seconds
        ):
            results.append((file_path, file_size))

        return results

    def clear_temp_files(self, days_old: int = 7, callback=None, max_workers: int = 4):
        """Clear temporary files in user directories that are older than specified days.

        Args:
            days_old: Remove files older than this many days
            callback: Progress callback function
            max_workers: Number of parallel scanning threads
        """
        temp_dirs = [
            os.path.join(self.user_home, "Library/Caches"),
            os.path.join(self.user_home, "Library/Logs"),
            os.path.join(self.user_home, "Downloads"),
            os.path.join(self.user_home, "Library/Application Support/Google/Chrome/Default/Cache"),
            os.path.join(self.user_home, "Library/Application Support/Firefox/Profiles"),
            os.path.join(self.user_home, "Library/Safari/Cache"),
            "/tmp",
            "/private/var/folders"  # System-wide temporary files
        ]

        total_size = 0
        total_files = 0
        age_limit_seconds = days_old * 86400

        self._log(f"Starting temp file cleanup. Days old: {days_old}")

        # Filter to existing directories
        existing_dirs = [d for d in temp_dirs if os.path.exists(d)]

        if callback:
            callback(f"Scanning {len(existing_dirs)} directories in parallel...")

        # Parallel scan phase
        files_to_delete = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_dir = {
                executor.submit(self._scan_temp_dir, d, age_limit_seconds): d
                for d in existing_dirs
            }

            for future in as_completed(future_to_dir):
                dir_name = future_to_dir[future]
                try:
                    results = future.result()
                    files_to_delete.extend(results)
                    if callback:
                        callback(f"Found {len(results)} files in {dir_name}")
                except Exception as e:
                    self._log(f"Warning: Error scanning {dir_name}: {e}")

        # Sequential delete phase (for accurate progress reporting)
        for file_path, file_size in files_to_delete:
            if self._delete_file(file_path, file_size, callback):
                total_size += file_size
                total_files += 1

        # Note: Stats are saved by the caller (GUI or CLI) after all operations complete
        # to avoid overwriting cumulative totals from multiple cleanup operations

        return total_files, total_size

    def clear_ds_store(self, callback=None):
        """Find and remove .DS_Store files"""
        total_files = 0
        total_size = 0

        # followlinks=False prevents symlink traversal attacks
        for root, dirs, files in os.walk(self.user_home, followlinks=False):
            for file in files:
                if file == ".DS_Store":
                    file_path = os.path.join(root, file)

                    # Skip symlinks for safety
                    if os.path.islink(file_path):
                        continue

                    try:
                        # Get size before removing
                        file_size = os.path.getsize(file_path) / (1024 * 1024)  # Size in MB
                        if self._delete_file(file_path, file_size, callback=callback):
                            total_files += 1
                            total_size += file_size
                    except (PermissionError, OSError):
                        pass

        self._log(f".DS_Store cleanup complete. Files: {total_files}, Size: {total_size:.2f} MB")

        
        return total_files, total_size

    def remove_language_files(self, callback=None):
        """Remove unused language files to save space"""
        language_dirs = [
            os.path.join(self.user_home, "Library/Application Support"),
            "/Applications"
        ]
        
        target_folders = [".lproj"]
        # Keep only English language files
        keep_languages = ["en.lproj", "en_US.lproj", "English.lproj", "Base.lproj"]
        
        total_files = 0
        total_size = 0
        
        for lang_dir in language_dirs:
            if not os.path.exists(lang_dir):
                continue
                
            if callback:
                callback(f"Scanning language files in: {lang_dir}")
                
            # followlinks=False prevents symlink traversal
            for root, dirs, files in os.walk(lang_dir, followlinks=False):
                for d in dirs:
                    if d.endswith(".lproj") and d not in keep_languages:
                        lang_path = os.path.join(root, d)

                        try:
                            # Check directory status FIRST, then calculate size
                            # This prevents TOCTOU race conditions
                            if not os.path.isdir(lang_path) or os.path.islink(lang_path):
                                continue

                            dir_size = self._get_dir_size(lang_path)
                            if self._delete_dir(lang_path, dir_size, callback=callback):
                                total_files += 1
                                total_size += dir_size
                        except (PermissionError, OSError) as e:
                            self._log(f"Warning: Could not process {lang_path}: {e}")

        self._log(f"Language file cleanup complete. Files: {total_files}, Size: {total_size:.2f} MB")
        return total_files, total_size

    def clear_app_caches(self, callback=None):
        """Clear application-specific caches"""
        app_caches = [
            os.path.join(self.user_home, "Library/Caches"),
            os.path.join(self.user_home, "Library/Application Support/Google/Chrome/Default/Cache"),
            os.path.join(self.user_home, "Library/Application Support/Firefox/Profiles"),
            os.path.join(self.user_home, "Library/Containers/com.apple.Safari/Data/Library/Caches"),
            os.path.join(self.user_home, "Library/Containers/com.apple.mail/Data/Library/Caches"),
            os.path.join(self.user_home, "Library/Containers/com.apple.Photos/Data/Library/Caches")
        ]
        
        total_files = 0
        total_size = 0
        
        for cache_dir in app_caches:
            if not os.path.exists(cache_dir):
                continue
                
            if callback:
                callback(f"Clearing cache: {cache_dir}")
                
            for root, dirs, files in os.walk(cache_dir, topdown=True):
                for file in files:
                    try:
                        file_path = os.path.join(root, file)
                        file_size = os.path.getsize(file_path) / (1024 * 1024)  # Size in MB

                        if self._delete_file(file_path, file_size, callback):
                            total_files += 1
                            total_size += file_size
                    except (PermissionError, OSError):
                        pass
        
        return total_files, total_size

    def clear_trash(self, callback=None):
        """Empty the Trash - single-pass implementation with dry-run support"""
        trash_dir = os.path.join(self.user_home, ".Trash")
        total_files = 0
        total_size = 0

        if not os.path.exists(trash_dir):
            return total_files, total_size

        if callback:
            callback("Emptying Trash...")

        try:
            for item in os.listdir(trash_dir):
                item_path = os.path.join(trash_dir, item)
                try:
                    if os.path.islink(item_path):
                        # Skip symlinks for safety
                        continue

                    if os.path.isdir(item_path):
                        dir_size = self._get_dir_size(item_path)
                        if self._delete_dir(item_path, dir_size, callback):
                            total_size += dir_size
                            total_files += 1
                    else:
                        file_size = os.path.getsize(item_path) / (1024 * 1024)
                        if self._delete_file(item_path, file_size, callback):
                            total_size += file_size
                            total_files += 1
                except (PermissionError, OSError) as e:
                    self._log(f"Warning: Could not process {item_path}: {e}")

        except (PermissionError, OSError) as e:
            self._log(f"Warning: Could not access trash directory: {e}")

        return total_files, total_size

    def flush_dns_cache(self, callback=None):
        """Flush DNS cache (macOS only)"""
        if platform.system() != "Darwin":
            if callback:
                callback("DNS cache flush is only available on macOS.")
            return False
            
        commands = [
            ["dscacheutil", "-flushcache"],
            ["sudo", "killall", "-HUP", "mDNSResponder"],
            ["sudo", "killall", "-HUP", "mDNSResponderHelper"]
        ]
        
        success = True
        for cmd in commands:
            try:
                if cmd[0] == "sudo":
                    if callback:
                        callback(f"To flush DNS cache, run: {' '.join(cmd)}")
                else:
                    subprocess.run(cmd, check=True, capture_output=True)
                    if callback:
                        callback(f"Executed: {' '.join(cmd)}")
            except subprocess.SubprocessError:
                success = False
                
        return success

    def purge_memory(self, callback=None):
        """Purge inactive memory (requires sudo)"""
        if callback:
            callback("To purge memory, run: sudo purge")
        return True

    def restart_dock_and_finder(self, callback=None):
        """Restart Dock and Finder to refresh UI"""
        commands = [
            ["killall", "Dock"],
            ["killall", "Finder"]
        ]
        
        success = True
        for cmd in commands:
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                if callback:
                    callback(f"Executed: {' '.join(cmd)}")
            except subprocess.SubprocessError:
                success = False
                
        return success

    def find_large_files(self, min_size_mb: float = 100, limit: int = 50, callback=None):
        """Find large files taking up space using heap for memory efficiency.

        Args:
            min_size_mb: Minimum file size in MB to consider
            limit: Maximum number of files to return (keeps only top N largest)
            callback: Progress callback function

        Returns:
            List of (file_path, size_mb) tuples, sorted largest first
        """
        # Use min-heap to efficiently keep track of top N largest files
        top_files = []

        if callback:
            callback("Searching for large files...")

        # Skip patterns - note: /Library/ would incorrectly skip ~/Library/
        # We want to skip /System and system Library, but include user Library
        system_skip = ["/System/", "/.Trash"]

        for root, dirs, files in os.walk(self.user_home):
            # Skip system directories and Trash
            if any(skip in root for skip in system_skip):
                continue

            # Skip the root /Library but not ~/Library
            if root.startswith("/Library/"):
                continue

            for file in files:
                try:
                    file_path = os.path.join(root, file)

                    # Skip symbolic links
                    if os.path.islink(file_path):
                        continue

                    # Get file size
                    file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB

                    # If file is larger than minimum size
                    if file_size >= min_size_mb:
                        if len(top_files) < limit:
                            # Heap not full, just add
                            heapq.heappush(top_files, (file_size, file_path))
                            if callback:
                                callback(f"Found large file: {file_path} ({file_size:.2f} MB)")
                        elif file_size > top_files[0][0]:
                            # File is larger than smallest in heap, replace
                            heapq.heapreplace(top_files, (file_size, file_path))
                            if callback:
                                callback(f"Found large file: {file_path} ({file_size:.2f} MB)")

                except (PermissionError, OSError):
                    continue

        # Convert heap to sorted list (largest first)
        result = [(path, size) for size, path in sorted(top_files, reverse=True)]

        return result

class CleanupGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("MacCleaner")
        self.root.geometry("700x600")
        self.root.resizable(True, True)
        
        # Set icon if available
        try:
            self.root.iconbitmap("icon.ico")  # Replace with your icon
        except:
            pass
            
        # Style
        self.style = ttk.Style()
        self.style.configure("TButton", padding=6, relief="flat", background="#3498db")
        self.style.configure("TFrame", background="#f5f5f5")
        self.style.configure("TLabel", background="#f5f5f5")
        self.style.configure("Header.TLabel", font=("Helvetica", 16, "bold"))
        self.style.configure("Subhead.TLabel", font=("Helvetica", 12, "bold"))
        self.style.configure("Info.TLabel", font=("Helvetica", 10))
        
        # Main frame
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Header
        header_frame = ttk.Frame(self.main_frame)
        header_frame.pack(fill=tk.X, pady=10)
        
        ttk.Label(header_frame, text="MacCleaner", style="Header.TLabel").pack(side=tk.LEFT)
        
        # System info frame
        self.info_frame = ttk.LabelFrame(self.main_frame, text="System Information", padding=10)
        self.info_frame.pack(fill=tk.X, pady=5)
        
        # System info labels
        self.disk_label = ttk.Label(self.info_frame, text="Disk Space: ", style="Info.TLabel")
        self.disk_label.grid(row=0, column=0, sticky="w", pady=2)
        
        self.memory_label = ttk.Label(self.info_frame, text="Memory: ", style="Info.TLabel")
        self.memory_label.grid(row=1, column=0, sticky="w", pady=2)
        
        self.cpu_label = ttk.Label(self.info_frame, text="CPU Usage: ", style="Info.TLabel")
        self.cpu_label.grid(row=2, column=0, sticky="w", pady=2)
        
        self.cleanup_label = ttk.Label(self.info_frame, text="Last Cleanup: ", style="Info.TLabel")
        self.cleanup_label.grid(row=3, column=0, sticky="w", pady=2)
        
        # Features frame
        features_frame = ttk.LabelFrame(self.main_frame, text="Cleanup Options", padding=10)
        features_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Checkboxes for options
        self.features = {
            "temp_files": {"text": "Temporary Files", "var": tk.BooleanVar(value=True)},
            "app_caches": {"text": "Application Caches", "var": tk.BooleanVar(value=True)},
            "ds_store": {"text": ".DS_Store Files", "var": tk.BooleanVar(value=True)},
            "language_files": {"text": "Unused Language Files", "var": tk.BooleanVar(value=False)},
            "trash": {"text": "Empty Trash", "var": tk.BooleanVar(value=True)},
            "dns_cache": {"text": "Flush DNS Cache", "var": tk.BooleanVar(value=False)},
            "memory": {"text": "Purge Memory", "var": tk.BooleanVar(value=False)},
            "restart_ui": {"text": "Restart Dock & Finder", "var": tk.BooleanVar(value=False)},
        }
        
        # Create checkboxes
        row = 0
        col = 0
        for key, feature in self.features.items():
            ttk.Checkbutton(features_frame, text=feature["text"], variable=feature["var"]).grid(
                row=row, column=col, sticky="w", padx=5, pady=5
            )
            col += 1
            if col > 1:
                col = 0
                row += 1
                
        # Age selector for temp files
        age_frame = ttk.Frame(features_frame)
        age_frame.grid(row=row+1, column=0, columnspan=2, sticky="w", padx=5, pady=10)
        
        ttk.Label(age_frame, text="Remove files older than: ").pack(side=tk.LEFT)
        
        self.age_var = tk.StringVar(value="7")
        age_combo = ttk.Combobox(age_frame, textvariable=self.age_var, width=5)
        age_combo['values'] = ('1', '3', '7', '14', '30')
        age_combo.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(age_frame, text="days").pack(side=tk.LEFT)
        
        # Analyze button
        analyze_btn = ttk.Button(features_frame, text="Analyze System", command=self.analyze_system)
        analyze_btn.grid(row=row+2, column=0, sticky="w", padx=5, pady=10)
        
        # Clean button
        clean_btn = ttk.Button(features_frame, text="Clean Now", command=self.start_cleanup)
        clean_btn.grid(row=row+2, column=1, sticky="w", padx=5, pady=10)
        
        # Progress frame
        progress_frame = ttk.LabelFrame(self.main_frame, text="Progress", padding=10)
        progress_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", 
                                            variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=5)
        
        # Status text
        self.status_text = tk.Text(progress_frame, height=10, wrap=tk.WORD)
        self.status_text.pack(fill=tk.BOTH, expand=True, pady=5)
        scrollbar = ttk.Scrollbar(self.status_text, orient="vertical", command=self.status_text.yview)
        self.status_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Initialize cleanup object
        self.cleaner = SystemCleanup()
        
        # Update system info
        self.update_system_info()
        
    def update_system_info(self):
        """Update the system information display"""
        info = self.cleaner.get_system_info()
        
        # Update disk info
        self.disk_label.config(text=f"Disk Space: {info['disk_used']:.1f}GB used of {info['disk_total']:.1f}GB ({info['disk_percent']:.1f}%)")
        
        # Update memory info
        self.memory_label.config(text=f"Memory: {info['memory_used']:.1f}GB used of {info['memory_total']:.1f}GB ({info['memory_percent']:.1f}%)")
        
        # Update CPU info
        self.cpu_label.config(text=f"CPU Usage: {info['cpu_percent']:.1f}%")
        
        # Update last cleanup info
        if info['last_cleanup']:
            last_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(info['last_cleanup']))
            self.cleanup_label.config(text=f"Last Cleanup: {last_time} • {info['files_cleaned']} files • {info['space_cleaned']:.2f} MB freed")
        else:
            self.cleanup_label.config(text="Last Cleanup: Never")
        
        # Schedule next update
        self.root.after(5000, self.update_system_info)
        
    def log_message(self, message):
        """Add message to the status text widget (thread-safe)"""
        def _update():
            self.status_text.insert(tk.END, message + "\n")
            self.status_text.see(tk.END)
        # Schedule on main thread for thread safety
        self.root.after(0, _update)

    def set_progress(self, value):
        """Set progress bar value (thread-safe)"""
        self.root.after(0, lambda: self.progress_var.set(value))
        
    def analyze_system(self):
        """Analyze the system and report potential cleanup options"""
        self.log_message("Starting system analysis...")
        self.progress_var.set(0)
        
        def run_analysis():
            # Check temp files
            self.log_message("Analyzing temporary files...")
            self.set_progress(20)

            # Find large files
            self.log_message("Scanning for large files...")
            large_files = self.cleaner.find_large_files(min_size_mb=100, callback=self.log_message)

            self.set_progress(80)

            # Report large files
            if large_files:
                self.log_message("\nLarge files found:")
                for i, (file_path, size) in enumerate(large_files[:10]):  # Show top 10
                    self.log_message(f"{i+1}. {file_path} ({size:.2f} MB)")

            self.set_progress(100)
            self.log_message("Analysis complete!")
            
        threading.Thread(target=run_analysis, daemon=True).start()
        
    def start_cleanup(self):
        """Start the cleanup process based on selected options"""
        self.log_message("Starting cleanup...")
        self.progress_var.set(0)
        
        try:
            days_old = int(self.age_var.get())
        except ValueError:
            days_old = 7
            
        # Get selected options
        options = {k: v["var"].get() for k, v in self.features.items()}
        
        def run_cleanup():
            total_steps = sum(1 for v in options.values() if v)

            if total_steps == 0:
                self.log_message("No cleanup options selected.")
                return

            current_step = 0
            total_files = 0
            total_size = 0

            # Clear temp files
            if options["temp_files"]:
                self.log_message("\nClearing temporary files...")
                files, size = self.cleaner.clear_temp_files(days_old=days_old, callback=self.log_message)
                total_files += files
                total_size += size
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Clear app caches
            if options["app_caches"]:
                self.log_message("\nClearing application caches...")
                files, size = self.cleaner.clear_app_caches(callback=self.log_message)
                total_files += files
                total_size += size
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Clear .DS_Store files
            if options["ds_store"]:
                self.log_message("\nRemoving .DS_Store files...")
                files, size = self.cleaner.clear_ds_store(callback=self.log_message)
                total_files += files
                total_size += size
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Remove language files
            if options["language_files"]:
                self.log_message("\nRemoving unused language files...")
                files, size = self.cleaner.remove_language_files(callback=self.log_message)
                total_files += files
                total_size += size
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Empty trash
            if options["trash"]:
                self.log_message("\nEmptying trash...")
                files, size = self.cleaner.clear_trash(callback=self.log_message)
                total_files += files
                total_size += size
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Flush DNS cache
            if options["dns_cache"]:
                self.log_message("\nFlushing DNS cache...")
                self.cleaner.flush_dns_cache(callback=self.log_message)
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Purge memory
            if options["memory"]:
                self.log_message("\nPurging inactive memory...")
                self.cleaner.purge_memory(callback=self.log_message)
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Restart Dock and Finder
            if options["restart_ui"]:
                self.log_message("\nRestarting Dock and Finder...")
                self.cleaner.restart_dock_and_finder(callback=self.log_message)
                current_step += 1
                self.set_progress((current_step / total_steps) * 100)

            # Final progress update
            self.set_progress(100)
            
            # Save cleanup stats
            self.cleaner.stats['total_files'] = total_files
            self.cleaner.stats['total_size'] = total_size
            self.cleaner.save_config()
            
            # Update system info
            self.update_system_info()
            
            # Show completion message
            self.log_message(f"\nCleanup completed!")
            self.log_message(f"Removed {total_files} files, freeing {total_size:.2f} MB of space.")
            
            # Show messagebox on completion (thread-safe)
            self.root.after(0, lambda: messagebox.showinfo(
                "Cleanup Complete",
                f"Successfully cleaned {total_files} files, freeing {total_size:.2f} MB of space."
            ))
            
        threading.Thread(target=run_cleanup, daemon=True).start()

def main():
    # Check if running in GUI or CLI mode
    if len(sys.argv) > 1:
        # CLI mode
        parser = argparse.ArgumentParser(description="Enhanced system cleanup utility for macOS")
        parser.add_argument("--days", type=int, default=7, help="Remove files older than this many days (default: 7)")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without actually deleting")
        parser.add_argument("--no-temp", action="store_true", help="Skip temporary files cleanup")
        parser.add_argument("--no-ds-store", action="store_true", help="Skip .DS_Store cleanup")
        parser.add_argument("--no-dns", action="store_true", help="Skip DNS cache flush")
        parser.add_argument("--no-memory", action="store_true", help="Skip memory purge")
        parser.add_argument("--no-system-cache", action="store_true", help="Skip system cache cleanup")
        parser.add_argument("--no-restart-ui", action="store_true", help="Skip restarting Dock and Finder")
        
        args = parser.parse_args()
        
        # Get user's home directory
        user_home = str(Path.home())
        
        print(f"Enhanced System Cleanup Utility for macOS")
        print(f"{'DRY RUN MODE' if args.dry_run else 'LIVE MODE'} - Files older than {args.days} days will be removed")

        cleaner = SystemCleanup(dry_run=args.dry_run)
        
        # Run selected cleanup operations
        if not args.no_temp:
            files, size = cleaner.clear_temp_files(days_old=args.days, callback=print)
            print(f"\nRemoved {files} temporary files ({size:.2f} MB)")
            
        if not args.no_ds_store:
            files, size = cleaner.clear_ds_store(callback=print)
            print(f"\nRemoved {files} .DS_Store files ({size:.2f} MB)")
            
        if not args.no_dns:
            cleaner.flush_dns_cache(callback=print)
        
        if not args.no_memory:
            cleaner.purge_memory(callback=print)
        
        if not args.no_system_cache:
            files, size = cleaner.clear_app_caches(callback=print)
            print(f"\nCleared {files} cache files ({size:.2f} MB)")
        
        if not args.no_restart_ui:
            cleaner.restart_dock_and_finder(callback=print)
            
        print("\nCleanup completed successfully!")
        print("Note: Some operations require admin privileges and will need to be")
        print("executed manually in Terminal with sudo access.")
    else:
        # GUI mode
        root = tk.Tk()
        app = CleanupGUI(root)
        root.mainloop()

if __name__ == "__main__":
    main()