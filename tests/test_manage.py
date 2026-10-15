"""Project bookkeeping: archive, restore, remove, ignore (spec Part A)."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from waypoint import chart, cli, logbook, manage, watch
from waypoint.paths import real

PROJECT = "# {name}\n- Path: {path}\n- Status: active\n- Summary: s\n\n## Now\n- Done: d\n- Next: n\n\n## Log\n{log}"
ENTRY = "\n### 2026-10-01 — codex\n- did a thing\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.code, self.lb = base / "code", base / "lb"
        (self.lb / "projects" / "_archive").mkdir(parents=True)
        (self.lb / "waypoint.toml").write_text(f'roots = ["{(self.code).as_posix()}"]\n', encoding="utf-8")
        for n in ("alpha", "beta"):
            (self.code / n).mkdir(parents=True)
        self.project("alpha", log=ENTRY)
        self.project("beta")
        patcher = patch("waypoint.clis.detected", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def project(self, name, archived=False, log=""):
        d = self.lb / "projects" / ("_archive" if archived else "")
        (d / f"{name}.md").write_text(PROJECT.format(name=name, path=self.code / name, log=log), encoding="utf-8")

    def names(self, archived=False):
        return sorted(p["name"] for p in logbook.load_projects(self.lb) if (p["status"] == "archived") == archived)

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main([*args, "--logbook", str(self.lb)])
        return out.getvalue()


class ArchiveRestore(Base):
    def test_archive_moves_the_file_and_keeps_text(self):
        before = (self.lb / "projects" / "alpha.md").read_text(encoding="utf-8")
        dest = manage.archive(self.lb, "ALPHA")  # case-insensitive
        self.assertEqual(dest, self.lb / "projects" / "_archive" / "alpha.md")
        self.assertEqual(dest.read_text(encoding="utf-8"), before)  # Status and log untouched
        self.assertEqual((self.names(), self.names(archived=True)), (["beta"], ["alpha"]))

    def test_restore_moves_it_back(self):
        manage.archive(self.lb, "alpha")
        dest = manage.restore(self.lb, "alpha")
        self.assertEqual(dest, self.lb / "projects" / "alpha.md")
        self.assertEqual(self.names(), ["alpha", "beta"])

    def test_unknown_name_lists_the_choices(self):
        with self.assertRaises(ValueError) as e:
            manage.archive(self.lb, "gamma")
        self.assertIn("alpha, beta", str(e.exception))

    def test_name_collisions_are_refused(self):
        self.project("Beta", archived=True)  # same name, other case, already archived
        with self.assertRaises(ValueError):
            manage.archive(self.lb, "beta")
        self.assertTrue((self.lb / "projects" / "beta.md").exists())
        with self.assertRaises(ValueError):
            manage.restore(self.lb, "beta")  # a live beta exists

    def test_restore_guard_holds_when_the_live_duplicate_is_unreadable(self):
        self.project("beta", archived=True)
        live = self.lb / "projects" / "beta.md"
        live.write_bytes(b"# beta\n\xff\xfe not utf-8\n")  # load_projects skips it, but the name is still taken
        arch = self.lb / "projects" / "_archive" / "beta.md"
        before = (live.read_bytes(), arch.read_bytes())
        with self.assertRaises(ValueError) as e:
            manage.restore(self.lb, "beta")
        self.assertIn("already has a file called beta.md", str(e.exception))
        self.assertEqual(before, (live.read_bytes(), arch.read_bytes()))

    def test_busy_logbook_changes_nothing(self):
        (self.lb / ".waypoint.lock").write_text("123", encoding="utf-8")
        with patch("waypoint.watch.LOCK_WAIT", 0.1), self.assertRaises(ValueError) as e:
            manage.archive(self.lb, "alpha")
        self.assertIn("busy", str(e.exception))
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())

    def test_cli_asks_and_reports(self):
        with patch("builtins.input", return_value="n"), self.assertRaises(SystemExit):
            self.run_cli("archive", "alpha")
        self.assertEqual(self.names(), ["alpha", "beta"])
        out = self.run_cli("archive", "alpha", "--yes")
        self.assertIn("Archived", out)
        self.assertIn("waypoint refresh", out)
        out = self.run_cli("restore", "alpha", "--yes")
        self.assertIn("Restored", out)


class LiveFileSaysArchived(Base):
    """Archived means 'lives in projects/_archive/', whatever the header's Status says."""

    def setUp(self):
        super().setUp()
        (self.code / "gamma").mkdir()
        self.project("gamma")
        f = self.lb / "projects" / "gamma.md"
        f.write_text(f.read_text(encoding="utf-8").replace("Status: active", "Status: archived"), encoding="utf-8")

    def test_archive_moves_it_and_restore_only_works_afterwards(self):
        with self.assertRaises(ValueError) as e:
            manage.restore(self.lb, "gamma")
        self.assertIn("no archived project", str(e.exception))
        self.assertEqual(manage.archive(self.lb, "gamma"), self.lb / "projects" / "_archive" / "gamma.md")
        self.assertEqual(manage.restore(self.lb, "gamma"), self.lb / "projects" / "gamma.md")

    def test_dashboard_lists_it_as_live_with_its_status_as_written(self):
        d = chart.build(self.lb, Path(self.tmp.name) / "home")
        live = {p["name"]: p["status"] for p in d["projects"]}
        self.assertEqual(live.get("gamma"), "archived")  # spec §2.1: unknown Status values are kept as written
        self.assertEqual(d["archived"], [])
        self.assertEqual(d["kpi"]["tracked"], 3)

    def test_log_watch_accepts_it(self):
        self.assertEqual(watch.append(self.lb, "gamma", "still live", "codex"), "logged")

    def test_loader_decides_archived_by_folder(self):
        manage.archive(self.lb, "alpha")
        flags = {p["name"]: p["archived"] for p in logbook.load_projects(self.lb)}
        self.assertEqual(flags, {"alpha": True, "beta": False, "gamma": False})

    def test_its_folder_still_counts_as_tracked(self):
        with self.assertRaises(ValueError) as e:
            manage.ignore(self.lb, self.code / "gamma")
        self.assertIn("projects/gamma.md", str(e.exception))
        self.assertIn("projects/gamma.md", manage.ignore_problem(self.lb, self.code / "gamma"))


class OsErrors(Base):
    def run_failing(self, *args):
        with self.assertRaises(SystemExit) as e:
            self.run_cli(*args, "--yes")
        msg = str(e.exception)
        self.assertTrue(msg.startswith("waypoint: couldn't "), msg)
        self.assertIn("PermissionError", msg)
        self.assertIn("nothing else changed", msg)
        self.assertNotIn("Traceback", msg)

    def test_archive_rename_denied(self):
        before = (self.lb / "projects" / "alpha.md").read_bytes()
        with patch.object(Path, "rename", side_effect=PermissionError("in use")):
            self.run_failing("archive", "alpha")
        self.assertEqual((self.lb / "projects" / "alpha.md").read_bytes(), before)
        self.assertFalse((self.lb / ".waypoint.lock").exists())

    def test_remove_delete_denied_changes_nothing_even_with_ignore(self):
        real_unlink = Path.unlink

        def deny_md(self, *a, **k):
            if self.suffix == ".md":
                raise PermissionError("in use")
            return real_unlink(self, *a, **k)

        with patch.object(Path, "unlink", deny_md):
            self.run_failing("remove", "beta", "--ignore")
        self.assertTrue((self.lb / "projects" / "beta.md").exists())
        self.assertFalse((self.lb / "projects" / "_ignore.txt").exists())

    def test_remove_ignore_write_failing_after_the_delete_says_what_happened(self):
        with patch("waypoint.manage._atomic_write", side_effect=PermissionError("in use")):
            with self.assertRaises(SystemExit) as e:
                self.run_cli("remove", "beta", "--ignore", "--yes")
        msg = str(e.exception)
        self.assertIn("removed projects/beta.md, but couldn't add", msg)
        self.assertIn('run: waypoint ignore "', msg)
        self.assertNotIn("nothing else changed", msg)
        self.assertFalse((self.lb / "projects" / "beta.md").exists())
        self.assertFalse((self.lb / ".waypoint.lock").exists())

    def test_remove_invalid_utf8_ignore_file_says_what_happened(self):
        (self.lb / "projects" / "_ignore.txt").write_bytes(b"\xff")
        with self.assertRaises(SystemExit) as e:
            self.run_cli("remove", "beta", "--ignore", "--yes")
        folder = (self.code / "beta").resolve()  # remove() reports the resolved folder: /private/var on macOS, long names for Windows CI's 8.3 paths
        self.assertEqual(str(e.exception), f'waypoint: removed projects/beta.md, but couldn\'t add {folder} to projects/_ignore.txt; run: waypoint ignore "{folder}".')
        self.assertFalse((self.lb / "projects" / "beta.md").exists())
        self.assertFalse((self.lb / ".waypoint.lock").exists())

    def test_ignore_write_denied(self):
        with patch("waypoint.manage._atomic_write", side_effect=PermissionError("in use")):
            self.run_failing("ignore", str(self.code / "zeta"))


class Remove(Base):
    def test_remove_deletes_and_can_ignore_the_folder(self):
        r = manage.remove(self.lb, "beta", ignore=True)
        self.assertEqual((r["logs"], r["file"].exists()), (0, False))
        self.assertIn(real(str(self.code / "beta")), logbook.load_ignore(self.lb))  # real(): CI temp paths may be 8.3 short names
        self.assertEqual(self.names(), ["alpha"])

    def test_cli_warns_about_history_and_suggests_archive(self):
        with patch("builtins.input", return_value="n"), self.assertRaises(SystemExit):
            out = io.StringIO()
            with redirect_stdout(out):
                cli.main(["remove", "alpha", "--logbook", str(self.lb)])
        self.assertIn("1 log entry", out.getvalue())
        self.assertIn("waypoint archive alpha", out.getvalue())
        self.assertTrue((self.lb / "projects" / "alpha.md").exists())
        out = self.run_cli("remove", "alpha", "--yes")
        self.assertIn("Removed", out)
        self.assertFalse((self.lb / "projects" / "alpha.md").exists())

    def test_archived_projects_are_not_removed_by_name(self):
        manage.archive(self.lb, "alpha")
        with self.assertRaises(ValueError):
            manage.remove(self.lb, "alpha")


class Ignore(Base):
    def test_ignore_and_undo_match_however_the_folder_is_spelled(self):
        scratch = self.code / "scratch"
        scratch.mkdir()
        self.assertTrue(manage.ignore(self.lb, str(scratch) + os.sep))
        self.assertFalse(manage.ignore(self.lb, scratch))  # already there
        self.assertEqual(len(logbook.load_ignore(self.lb)), 1)
        spelled = str(scratch).upper() if os.name == "nt" else str(scratch)
        self.assertTrue(manage.ignore(self.lb, spelled, undo=True))
        self.assertEqual(logbook.load_ignore(self.lb), set())

    def test_undo_keeps_comments_and_other_lines(self):
        f = self.lb / "projects" / "_ignore.txt"
        f.write_text(f"# not projects\n{self.code / 'x'}\n{self.code / 'y'}\n", encoding="utf-8")
        manage.ignore(self.lb, self.code / "x", undo=True)
        self.assertEqual(f.read_text(encoding="utf-8"), f"# not projects\n{self.code / 'y'}\n")

    def test_a_tracked_folder_is_not_ignored(self):
        with self.assertRaises(ValueError) as e:
            manage.ignore(self.lb, self.code / "alpha")
        self.assertIn("projects/alpha.md", str(e.exception))

    def test_crlf_ignore_file_keeps_crlf(self):
        f = self.lb / "projects" / "_ignore.txt"
        # ignore() writes the resolved folder: /private/var on macOS, long names for Windows CI's 8.3 temp paths
        x, y = str(self.code / "x").encode(), str((self.code / "y").resolve()).encode()
        f.write_bytes(b"# not projects\r\n" + x + b"\r\n")
        manage.ignore(self.lb, self.code / "y")
        self.assertEqual(f.read_bytes(), b"# not projects\r\n" + x + b"\r\n" + y + b"\r\n")
        manage.ignore(self.lb, self.code / "x", undo=True)
        self.assertEqual(f.read_bytes(), b"# not projects\r\n" + y + b"\r\n")

    def test_cli_refuses_a_tracked_folder_before_asking(self):
        with patch("builtins.input", side_effect=AssertionError("must not ask")), self.assertRaises(SystemExit) as e:
            self.run_cli("ignore", str(self.code / "alpha"))
        self.assertIn("projects/alpha.md", str(e.exception))

    def test_cli(self):
        out = self.run_cli("ignore", str(self.code / "zeta"), "--yes")
        self.assertIn("Ignoring", out)
        out = self.run_cli("ignore", str(self.code / "zeta"), "--undo", "--yes")
        self.assertIn("No longer ignoring", out)


if __name__ == "__main__":
    unittest.main()
