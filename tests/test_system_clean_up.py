import importlib.machinery
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True

_APP_PATH = Path(__file__).resolve().parent.parent / "Mac Clean.command"
_LOADER = importlib.machinery.SourceFileLoader("mac_clean", str(_APP_PATH))
_SPEC = importlib.util.spec_from_loader("mac_clean", _LOADER)
assert _SPEC is not None
mac_clean = importlib.util.module_from_spec(_SPEC)
sys.modules["mac_clean"] = mac_clean
_LOADER.exec_module(mac_clean)


class SystemCleanupTests(unittest.TestCase):
    def make_cleaner(self, root, dry_run=False):
        cleaner = object.__new__(mac_clean.SystemCleanup)
        cleaner.dry_run = dry_run
        cleaner.write_state = False
        cleaner._log_handle = None
        cleaner._log_count = 0
        cleaner.allowed_roots = [os.path.realpath(root)]
        cleaner.permission_denied = []
        cleaner._permission_denied_set = set()
        cleaner.warnings = []
        cleaner._running_processes_cache = set()
        cleaner._claimed_dirs = set()
        cleaner.stats = {
            "total_size": 0.0,
            "total_files": 0,
            "last_size": 0.0,
            "last_files": 0,
            "last_run": None,
        }
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
        with patch.object(mac_clean.subprocess, "run") as run:
            self.assertFalse(cleaner.flush_dns())
            self.assertFalse(cleaner.free_ram())
            self.assertFalse(cleaner.reset_quicklook())
        run.assert_not_called()

    def test_dry_run_does_not_create_state_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            log_dir = Path(directory) / "logs"
            log_path = log_dir / "cleanup.log"
            with patch.object(mac_clean, "CONFIG_PATH", str(config)), \
                    patch.object(mac_clean, "LOG_DIR", str(log_dir)), \
                    patch.object(mac_clean, "LOG_PATH", str(log_path)), \
                    patch.object(mac_clean.platform, "system", return_value="Darwin"):
                cleaner = mac_clean.SystemCleanup(dry_run=True)
            self.assertFalse(config.exists())
            self.assertFalse(log_dir.exists())
            cleaner.close()

    def test_log_cleanup_keeps_recent_files(self):
        with tempfile.TemporaryDirectory() as root:
            old_file = Path(root) / "old.crash"
            recent_file = Path(root) / "recent.crash"
            old_file.write_bytes(b"old")
            recent_file.write_bytes(b"recent")
            old_time = time.time() - (31 * mac_clean.DAY)
            os.utime(old_file, (old_time, old_time))
            cleaner = self.make_cleaner(root)
            with patch.object(mac_clean, "LOG_CLEANUP_DIRS", [root]):
                files, _ = cleaner.clean_logs(days=30)

            self.assertEqual(files, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(recent_file.exists())

    def test_cache_cleanup_respects_age_threshold_and_prunes_empty_dirs(self):
        with tempfile.TemporaryDirectory() as root:
            nested = Path(root) / "sub" / "deep"
            nested.mkdir(parents=True)
            old_file = nested / "old.cache"
            recent_file = Path(root) / "recent.cache"
            old_file.write_bytes(b"old")
            recent_file.write_bytes(b"recent")
            old_time = time.time() - (8 * mac_clean.DAY)
            os.utime(old_file, (old_time, old_time))
            cleaner = self.make_cleaner(root)
            with patch.object(mac_clean, "CACHE_DIRS", [root]):
                files, _ = cleaner.clean_caches(days=7)

            self.assertEqual(files, 1)
            self.assertFalse(old_file.exists())
            self.assertFalse(nested.exists())
            self.assertTrue(Path(root).exists())
            self.assertTrue(recent_file.exists())

    def test_unsafe_firefox_profile_and_broad_logs_are_not_cache_targets(self):
        firefox_profiles = os.path.join(
            mac_clean.HOME, "Library/Application Support/Firefox/Profiles"
        )
        broad_logs = os.path.join(mac_clean.HOME, "Library/Logs")
        self.assertNotIn(firefox_profiles, mac_clean.CACHE_DIRS)
        self.assertNotIn(broad_logs, mac_clean.CACHE_DIRS)

    def test_optional_cache_pass_does_not_mutate_base_targets_and_dedupes(self):
        with tempfile.TemporaryDirectory() as root:
            cache_file = Path(root) / "cache.bin"
            cache_file.write_bytes(b"cache")
            cleaner = self.make_cleaner(root, dry_run=True)
            base_targets = [root]
            with patch.object(mac_clean, "CACHE_DIRS", base_targets), \
                    patch.object(mac_clean, "OPTIONAL_CACHE_DIRS", [root]), \
                    patch.object(mac_clean, "TEMP_CACHE_DIRS", [root]):
                files, _ = cleaner.clean_caches(days=0, include_temp=True, include_optional=True)
            self.assertEqual(base_targets, [root])
            # Deduplication ensures the same root is only counted once in dry-run
            self.assertEqual(files, 1)

    def test_native_system_info_works_without_psutil(self):
        cleaner = self.make_cleaner(tempfile.gettempdir())
        vm_stat_sample = (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages active: 100000.\n"
            "Pages wired down: 50000.\n"
            "Pages occupied by compressor: 25000.\n"
            "Pages free: 50000.\n"
        )

        def fake_check_output(cmd, **kwargs):
            if cmd == ["sysctl", "-n", "hw.memsize"]:
                return "8589934592\n"
            if cmd == ["vm_stat"]:
                return vm_stat_sample
            if cmd == ["sysctl", "-n", "hw.ncpu"]:
                return "8\n"
            if cmd == ["ps", "-A", "-o", "%cpu="]:
                return "12,5\n27.5\n"
            raise AssertionError(f"Unexpected cmd: {cmd}")

        with patch.object(mac_clean, "psutil", None), \
                patch.object(mac_clean.subprocess, "check_output", side_effect=fake_check_output):
            info = cleaner.system_info()

        self.assertEqual(info["metrics_backend"], "native")
        self.assertAlmostEqual(info["mem_total_gb"], 8.0, places=2)
        self.assertGreater(info["mem_used_gb"], 0.0)
        self.assertAlmostEqual(info["cpu_pct"], 5.0, places=2)

    def test_browser_games_scan_and_clean_preserves_profile_data(self):
        with tempfile.TemporaryDirectory() as home:
            chrome_app = Path(home) / "Library/Application Support/Google/Chrome/Default"
            chrome_cache = Path(home) / "Library/Caches/Google/Chrome/Default"

            # Big 3 MB IndexedDB game (grouped .leveldb + .blob)
            idb_lvl = chrome_app / "IndexedDB/https_crazygames.com_0.indexeddb.leveldb"
            idb_blob = chrome_app / "IndexedDB/https_crazygames.com_0.indexeddb.blob"
            idb_lvl.mkdir(parents=True)
            idb_blob.mkdir(parents=True)
            (idb_lvl / "0001.ldb").write_bytes(b"x" * (1 * mac_clean.MB))
            (idb_blob / "game.wasm").write_bytes(b"y" * (2 * mac_clean.MB))

            # Tiny 10 KB IndexedDB app (should be spared by min_mb=1)
            small_idb = chrome_app / "IndexedDB/https_notes.example.com_0.indexeddb.leveldb"
            small_idb.mkdir(parents=True)
            (small_idb / "small.ldb").write_bytes(b"z" * 10240)

            # WebStorage / OPFS bucket with QuotaManager SQLite mapping
            ws_dir = chrome_app / "WebStorage"
            bucket_dir = ws_dir / "42"
            bucket_dir.mkdir(parents=True)
            (bucket_dir / "assets.pak").write_bytes(b"a" * (2 * mac_clean.MB))
            conn = sqlite3.connect(str(ws_dir / "QuotaManager"))
            conn.execute("CREATE TABLE buckets (id INTEGER PRIMARY KEY, storage_key TEXT)")
            conn.execute("INSERT INTO buckets (id, storage_key) VALUES (42, 'https://poki.com/')")
            conn.commit()
            conn.close()

            # Service Worker CacheStorage with index.txt origin
            cs_dir = chrome_app / "Service Worker/CacheStorage/abcdef1234567890"
            cs_dir.mkdir(parents=True)
            (cs_dir / "index.txt").write_bytes(b"\x00\x01https://itch.io/game\x00")
            (cs_dir / "bundle.bin").write_bytes(b"b" * (2 * mac_clean.MB))

            # WASM code cache
            wasm_dir = chrome_cache / "Code Cache/wasm"
            wasm_dir.mkdir(parents=True)
            (wasm_dir / "compiled.wasm").write_bytes(b"w" * (2 * mac_clean.MB))

            # Sensitive browser data that MUST NEVER be touched
            cookies = chrome_app / "Cookies"
            bookmarks = chrome_app / "Bookmarks"
            local_storage = chrome_app / "Local Storage/leveldb"
            local_storage.mkdir(parents=True)
            cookies.write_bytes(b"secret-cookie")
            bookmarks.write_bytes(b"my-bookmarks")
            (local_storage / "0001.ldb").write_bytes(b"keep-me")

            cleaner = self.make_cleaner(home)
            with patch.object(mac_clean, "HOME", home), \
                    patch.object(mac_clean, "FIREFOX_APP_SUPPORT_BASE", os.path.join(home, "no-ff")):
                items = cleaner.scan_browser_game_storage(min_mb=1.0)
                origins = {(i.kind, i.origin) for i in items}
                self.assertIn(("IndexedDB", "https://crazygames.com"), origins)
                self.assertIn(("WebStorage/OPFS", "https://poki.com"), origins)
                self.assertIn(("CacheStorage", "https://itch.io/game"), origins)
                self.assertIn(("WASM Code Cache", "(compiled WASM games)"), origins)
                self.assertNotIn(("IndexedDB", "https://notes.example.com"), origins)

                # Filter to just crazygames first
                units, mb = cleaner.clean_browser_games(min_mb=1.0, origin_filter="crazygames")
                self.assertEqual(units, 1)
                self.assertGreaterEqual(mb, 2.9)
                self.assertFalse(idb_lvl.exists())
                self.assertFalse(idb_blob.exists())
                self.assertTrue(bucket_dir.exists())

                # Now clean remaining heavy game storage
                units2, _ = cleaner.clean_browser_games(min_mb=1.0)
                self.assertEqual(units2, 3)
                self.assertFalse(bucket_dir.exists())
                self.assertFalse(cs_dir.exists())
                self.assertFalse((wasm_dir / "compiled.wasm").exists())

            # Tiny app + sensitive browser files untouched
            self.assertTrue(small_idb.exists())
            self.assertTrue(cookies.exists())
            self.assertTrue(bookmarks.exists())
            self.assertTrue((local_storage / "0001.ldb").exists())

    def test_browser_games_skips_running_browser_unless_forced(self):
        with tempfile.TemporaryDirectory() as home:
            idb_dir = Path(home) / "Library/Application Support/Google/Chrome/Default/IndexedDB/https_game.io_0.indexeddb.blob"
            idb_dir.mkdir(parents=True)
            (idb_dir / "big.data").write_bytes(b"g" * (2 * mac_clean.MB))

            cleaner = self.make_cleaner(home)
            cleaner._running_processes_cache = {"Google Chrome"}

            with patch.object(mac_clean, "HOME", home), \
                    patch.object(mac_clean, "FIREFOX_APP_SUPPORT_BASE", os.path.join(home, "no-ff")):
                units, mb = cleaner.clean_browser_games(min_mb=1.0, force_browsers=False)
                self.assertEqual(units, 0)
                self.assertEqual(mb, 0.0)
                self.assertTrue(idb_dir.exists())
                self.assertTrue(any("Google Chrome is running" in w for w in cleaner.warnings))

                units_forced, mb_forced = cleaner.clean_browser_games(min_mb=1.0, force_browsers=True)
                self.assertEqual(units_forced, 1)
                self.assertGreaterEqual(mb_forced, 1.9)
                self.assertFalse(idb_dir.exists())

    def test_browser_caches_keeps_updater_metadata(self):
        with tempfile.TemporaryDirectory() as home:
            crx_dir = Path(home) / "Library/Application Support/Google/GoogleUpdater/crx_cache"
            crx_dir.mkdir(parents=True)
            meta = crx_dir / "metadata.json"
            blob = crx_dir / "old_update_blob"
            meta.write_text("{}")
            blob.write_bytes(b"0" * 1024)

            cleaner = self.make_cleaner(home)
            with patch.object(mac_clean, "HOME", home), \
                    patch.object(mac_clean, "CHROME_UPDATER_CRX_CACHE", str(crx_dir)), \
                    patch.object(mac_clean, "SAFARI_CACHE_DIRS", ()), \
                    patch.object(mac_clean, "FIREFOX_CACHES_BASE", os.path.join(home, "no-ff")):
                files, _ = cleaner.clean_browser_caches(days=0)

            self.assertEqual(files, 1)
            self.assertTrue(meta.exists())
            self.assertFalse(blob.exists())

    def test_permission_denied_collapses_children(self):
        cleaner = self.make_cleaner("/tmp")
        cleaner._record_permission_denied("/Users/test/Library/Application Support/Google/Chrome")
        cleaner._record_permission_denied("/Users/test/Library/Application Support/Google/Chrome/Default")
        self.assertEqual(
            cleaner.permission_denied,
            ["/Users/test/Library/Application Support/Google/Chrome"],
        )


class ParserAndRoutingTests(unittest.TestCase):
    def test_days_must_be_non_negative(self):
        parser = mac_clean.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--days", "-1"])

    def test_browser_min_mb_must_be_non_negative(self):
        parser = mac_clean.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--browser-min-mb", "-5"])

    def test_skip_and_only_validate_operation_names(self):
        parser = mac_clean.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--skip", "not-an-operation"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--only", "not-an-operation"])

        args = parser.parse_args([
            "--full", "--purge-memory", "--skip", "dns",
            "--only", "browsers", "browser_games",
            "--browser-days", "0",
            "--browser-min-mb", "50",
            "--browser-origin", "crazygames",
            "--force-browsers", "--json",
        ])
        self.assertTrue(args.full)
        self.assertTrue(args.purge_memory)
        self.assertEqual(args.skip, ["dns"])
        self.assertEqual(args.only, ["browsers", "browser_games"])
        self.assertEqual(args.browser_days, 0)
        self.assertEqual(args.browser_min_mb, 50.0)
        self.assertEqual(args.browser_origin, "crazygames")
        self.assertTrue(args.force_browsers)
        self.assertTrue(args.json)

    def test_single_file_entrypoint_routing(self):
        parser = mac_clean.build_parser()
        # Double-click / no args in TTY -> interactive UI
        self.assertTrue(mac_clean._should_run_ui(parser.parse_args([]), [], stdin_is_tty=True))
        # No args without TTY (e.g. cron/pipe) -> CLI
        self.assertFalse(mac_clean._should_run_ui(parser.parse_args([]), [], stdin_is_tty=False))
        # Explicit UI mode -> interactive UI runner
        self.assertTrue(
            mac_clean._should_run_ui(
                parser.parse_args(["--mode", "dry_run", "--non-interactive"]),
                ["--mode", "dry_run", "--non-interactive"],
                stdin_is_tty=False,
            )
        )
        # Explicit CLI flags -> CLI runner
        for cli_args in (["--analyze"], ["--dry-run"], ["--cli"], ["--info"], ["--full"]):
            self.assertFalse(
                mac_clean._should_run_ui(parser.parse_args(cli_args), cli_args, stdin_is_tty=True)
            )


class LauncherUITests(unittest.TestCase):
    def test_box_borders_align_exact_width(self):
        expected = mac_clean.BOX_W + 2
        self.assertEqual(mac_clean._vis_len(mac_clean._box_top("MAC CLEAN")), expected)
        self.assertEqual(mac_clean._vis_len(mac_clean._box_top()), expected)
        self.assertEqual(mac_clean._vis_len(mac_clean._box_mid()), expected)
        self.assertEqual(mac_clean._vis_len(mac_clean._box_row("hello")), expected)
        self.assertEqual(mac_clean._vis_len(mac_clean._box_bot()), expected)

    def test_wrapped_box_rows_never_exceed_box_width(self):
        long_msg = "Google Chrome is running — close it (or pass --force-browsers) to safely clean 6.42 GB of browser game storage."
        rows = mac_clean._box_wrapped_rows("! ", long_msg, border_color="")
        self.assertGreaterEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(mac_clean._vis_len(r), mac_clean.BOX_W + 2)

    def test_detect_running_browsers(self):
        cleaner = object.__new__(mac_clean.SystemCleanup)
        cleaner._running_processes_cache = {"Google Chrome", "Safari"}
        with patch.object(cleaner, "_is_process_running", side_effect=lambda p: p in {"Google Chrome", "Safari"}):
            found = mac_clean.detect_running_browsers(cleaner)
        names = [name for name, _ in found]
        self.assertIn("Google Chrome", names)
        self.assertIn("Safari", names)
        self.assertNotIn("Firefox", names)

    def test_ask_macos_popup_handles_confirm_and_cancel(self):
        with patch.object(mac_clean.subprocess, "check_output", return_value="Grant Permission\n"):
            self.assertTrue(
                mac_clean.ask_macos_popup("Title", "Body", confirm_label="Grant Permission")
            )
        with patch.object(
            mac_clean.subprocess,
            "check_output",
            side_effect=mac_clean.subprocess.CalledProcessError(1, "osascript"),
        ):
            self.assertFalse(
                mac_clean.ask_macos_popup("Title", "Body", confirm_label="Grant Permission")
            )

    def test_admin_maintenance_popup_skips_when_declined(self):
        cleaner = object.__new__(mac_clean.SystemCleanup)
        cleaner.dry_run = False
        cleaner._log_handle = None
        cleaner.warnings = []
        with patch.object(mac_clean, "ask_macos_popup", return_value=False), \
                patch.object(mac_clean, "_local_dns_flush", return_value=True) as local_dns, \
                patch.object(cleaner, "run_admin_maintenance") as admin_run:
            result = mac_clean._admin_maintenance_with_popup(cleaner, interactive=True)
        self.assertFalse(result)
        admin_run.assert_not_called()
        local_dns.assert_called_once()

    def test_admin_maintenance_dry_run_never_prompts(self):
        cleaner = object.__new__(mac_clean.SystemCleanup)
        cleaner.dry_run = True
        cleaner._log_handle = None
        with patch.object(mac_clean, "ask_macos_popup") as popup, \
                patch.object(mac_clean.subprocess, "run") as sub_run:
            self.assertFalse(mac_clean._admin_maintenance_with_popup(cleaner, interactive=True))
            self.assertFalse(cleaner.run_admin_maintenance())
        popup.assert_not_called()
        sub_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
