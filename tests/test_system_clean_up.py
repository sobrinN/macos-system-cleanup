import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import system_clean_up as cleaner_module


class SystemCleanupTests(unittest.TestCase):
    def make_cleaner(self, root, dry_run=False):
        cleaner = object.__new__(cleaner_module.SystemCleanup)
        cleaner.dry_run = dry_run
        cleaner.write_state = False
        cleaner._log_handle = None
        cleaner.allowed_roots = [os.path.realpath(root)]
        cleaner.stats = {"total_size": 0, "total_files": 0, "last_run": None}
        return cleaner

    def test_safe_delete_cannot_escape_allowed_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            target = Path(root) / "cache.bin"
            target.write_bytes(b"cache")
            external = Path(outside) / "keep.bin"
            external.write_bytes(b"keep")
            cleaner = self.make_cleaner(root)

            self.assertTrue(cleaner._safe_delete(str(target)))
            self.assertFalse(target.exists())
            self.assertFalse(cleaner._safe_delete(str(external)))
            self.assertTrue(external.exists())
            self.assertTrue(cleaner._is_within_allowed(root))
            self.assertFalse(cleaner._is_safe(root))

    def test_directory_delete_does_not_follow_nested_symlink(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            target_dir = Path(root) / "cache"
            target_dir.mkdir()
            external = Path(outside) / "keep.bin"
            external.write_bytes(b"keep")
            (target_dir / "external-link").symlink_to(external)
            cleaner = self.make_cleaner(root)

            self.assertTrue(cleaner._safe_delete(str(target_dir)))
            self.assertFalse(target_dir.exists())
            self.assertTrue(external.exists())

    def test_dry_run_skips_system_commands(self):
        cleaner = self.make_cleaner(tempfile.gettempdir(), dry_run=True)
        with patch.object(cleaner_module.subprocess, "run") as run:
            self.assertFalse(cleaner.flush_dns())
            self.assertFalse(cleaner.free_ram())
            self.assertFalse(cleaner.reset_quicklook())
        run.assert_not_called()

    def test_dry_run_does_not_create_state_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            log_dir = Path(directory) / "logs"
            log_path = log_dir / "cleanup.log"
            with patch.object(cleaner_module, "CONFIG_PATH", str(config)), \
                    patch.object(cleaner_module, "LOG_DIR", str(log_dir)), \
                    patch.object(cleaner_module, "LOG_PATH", str(log_path)), \
                    patch.object(cleaner_module.platform, "system", return_value="Darwin"):
                cleaner = cleaner_module.SystemCleanup(dry_run=True)
            self.assertFalse(config.exists())
            self.assertFalse(log_dir.exists())
            cleaner.close()

    def test_log_cleanup_keeps_recent_files(self):
        with tempfile.TemporaryDirectory() as root:
            old_file = Path(root) / "old.crash"
            recent_file = Path(root) / "recent.crash"
            old_file.write_bytes(b"old")
            recent_file.write_bytes(b"recent")
            old_time = time.time() - (31 * cleaner_module.DAY)
            os.utime(old_file, (old_time, old_time))
            cleaner = self.make_cleaner(root)
            with patch.object(cleaner_module, "LOG_CLEANUP_DIRS", [root]):
                files, _ = cleaner.clean_logs(days=30)

            self.assertEqual(files, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(recent_file.exists())

    def test_cache_cleanup_respects_age_threshold(self):
        with tempfile.TemporaryDirectory() as root:
            old_file = Path(root) / "old.cache"
            recent_file = Path(root) / "recent.cache"
            old_file.write_bytes(b"old")
            recent_file.write_bytes(b"recent")
            old_time = time.time() - (8 * cleaner_module.DAY)
            os.utime(old_file, (old_time, old_time))
            cleaner = self.make_cleaner(root)
            with patch.object(cleaner_module, "CACHE_DIRS", [root]):
                files, _ = cleaner.clean_caches(days=7)

            self.assertEqual(files, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(recent_file.exists())

    def test_unsafe_firefox_profile_and_broad_logs_are_not_cache_targets(self):
        firefox_profiles = os.path.join(
            cleaner_module.HOME, "Library/Application Support/Firefox/Profiles"
        )
        broad_logs = os.path.join(cleaner_module.HOME, "Library/Logs")
        self.assertNotIn(firefox_profiles, cleaner_module.CACHE_DIRS)
        self.assertNotIn(broad_logs, cleaner_module.CACHE_DIRS)

    def test_optional_cache_pass_does_not_mutate_base_targets(self):
        with tempfile.TemporaryDirectory() as root:
            cache_file = Path(root) / "cache.bin"
            cache_file.write_bytes(b"cache")
            cleaner = self.make_cleaner(root, dry_run=True)
            base_targets = [root]
            with patch.object(cleaner_module, "CACHE_DIRS", base_targets), \
                    patch.object(cleaner_module, "OPTIONAL_CACHE_DIRS", [root]), \
                    patch.object(cleaner_module, "TEMP_CACHE_DIRS", [root]):
                cleaner.clean_caches(include_temp=True, include_optional=True)
            self.assertEqual(base_targets, [root])


class ParserTests(unittest.TestCase):
    def test_days_must_be_non_negative(self):
        parser = cleaner_module.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--days", "-1"])

    def test_skip_validates_operation_names(self):
        parser = cleaner_module.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--skip", "not-an-operation"])

        args = parser.parse_args(["--full", "--purge-memory", "--skip", "dns"])
        self.assertTrue(args.full)
        self.assertTrue(args.purge_memory)
        self.assertEqual(args.skip, ["dns"])


if __name__ == "__main__":
    unittest.main()
