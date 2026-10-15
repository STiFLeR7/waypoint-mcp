"""Append one log entry to a project file: the local `log_watch` (LOGBOOK-SPEC §2.2, §8.2).

Safe against concurrent writers: a lock file serialises Waypoint's own writers, and a compare-and-swap (re-hash the
file just before replacing it) catches anything else, such as an agent editing the file directly.
"""
import hashlib
import os
import re
import tempfile
import time
from contextlib import nullcontext
from datetime import date, timedelta
from pathlib import Path

from . import logbook

TOOL_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
SUMMARY_MAX = 2000
NOW_MAX = 300
_NOW_ORDER = ("Done", "Next", "Updated")
LOCK_WAIT, LOCK_STALE = 5.0, 30.0
CRLF, CR, LF = chr(13) + chr(10), chr(13), chr(10)
LOG_LINE = re.compile(r"(^|\r\n|\r|\n)## Log[ \t]*(\r\n|\r|\n|$)", re.I)


class WatchError(Exception):
    """A refusal the caller should show as-is (no paths in the message)."""


def check_input(summary, tool, day, today: date):
    if not isinstance(tool, str) or not TOOL_ID.match(tool) or tool not in logbook.KNOWN_TOOLS:
        raise WatchError(f"tool must be one of: {', '.join(logbook.KNOWN_TOOLS)}.")
    if not isinstance(summary, str) or not summary.strip():
        raise WatchError("summary is required: 1–3 short lines on what was done.")
    if len(summary) > SUMMARY_MAX:
        raise WatchError(f"summary is {len(summary)} characters; the cap is {SUMMARY_MAX}. Shorten it and link details.")
    if day is None:
        return today.isoformat()
    try:
        d = date.fromisoformat(day) if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else None
    except ValueError:
        d = None
    if d is None or abs(d - today) > timedelta(days=1):
        raise WatchError("date must be YYYY-MM-DD within one day of today.")
    return d.isoformat()


def entry_block(day, tool, summary, eol):
    """Heading plus every line quoted with "> " (§2.2): pasted text can never become a heading or field."""
    text = re.sub(r"[\x00-\x09\x0b-\x1f\x7f-\x9f  ]", " ", summary.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [l.strip() for l in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        raise WatchError("summary has no printable text.")
    return eol.join([f"### {day} — {tool}", *[f"> {l}" if l else ">" for l in lines]]) + eol


def insert_entry(text, day, tool, summary):
    """New file text with the entry below the first `## Log` line (one blank line each side), or None when an
    identical entry (same date, tool and text) already exists on that date."""
    m = LOG_LINE.search(text)
    if not m:
        raise WatchError("this project file has no `## Log` line, so there is nowhere to log. Nothing was written.")
    at = m.end()
    eol = m[2] or (re.search(r"\r\n|\r|\n", text) or [None])[0] or "\n"
    block = entry_block(day, tool, summary, eol)
    lf = lambda t: t.replace(CRLF, LF).replace(CR, LF)  # the reader's view; the file keeps its own endings
    mine = logbook.parse_log(lf(block))[0]
    if any(e["date"] == day and e["tool"] == tool and e["text"] == mine["text"] for e in logbook.parse_log(lf(text[at:]))):
        return None
    rest = re.sub(r"^(?:[ \t]*(?:\r\n|\r|\n))+", "", text[at:])
    return text[:at] + ("" if m[2] else eol) + eol + block + (eol if rest.strip() else "") + rest


def clean_now(value, name):
    """One `## Now` value for log_watch (§8.3): a single line of 1–300 characters that doesn't start with # or >."""
    if not isinstance(value, str):
        raise WatchError(f"{name} must be text.")
    text = " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value).split())  # split() also breaks on U+2028/2029
    if not text:
        raise WatchError(f"{name} has no printable text.")
    if len(text) > NOW_MAX:
        raise WatchError(f"{name} is {len(text)} characters; the cap is {NOW_MAX}. Keep it to one short line.")
    if text[0] in "#>":
        raise WatchError(f"{name} can't start with # or >; write it as plain text.")
    return text


def set_now(text, day, fields):
    """`text` with the `## Now` lines in `fields` ({"Done": ..., "Next": ...}, already cleaned) replaced and
    `- Updated: day` set (§8.3). The line replaced is the one a reader reads (§2.1: the first with text, else the
    first bare one); a missing line goes right after the Now fields before it (Done, Next, Updated), else at the top
    of Now; a missing `## Now` is created just above `## Log`. Every other byte and the line endings stay as they were."""
    m = LOG_LINE.search(text)
    if not m:
        raise WatchError("this project file has no `## Log` line, so there is nowhere to log. Nothing was written.")
    cut = m.start() + len(m[1])  # the head: everything before the `## Log` line, including its line break
    eol = m[1] or m[2] or (re.search(r"\r\n|\r|\n", text) or [None])[0] or "\n"
    parts = re.split(r"(\r\n|\r|\n)", text[:cut])
    lines = [[l, s] for l, s in zip(parts[0::2], parts[1::2] + [""])]
    now = next((i for i, (l, _) in enumerate(lines) if l.startswith("## ") and l[3:].strip().lower() == "now"), None)
    if now is None:
        now = len(lines) - 1
        lines[now:now] = [["## Now", eol], ["", eol]]
    end = next((i for i in range(now + 1, len(lines)) if lines[i][0].startswith("## ")), len(lines))
    values, after = {**fields, "Updated": day}, now
    for name in _NOW_ORDER:
        body = range(now + 1, end)
        hit = next((i for i in body if lines[i][0].startswith(f"- {name}:") and len(lines[i][0]) > len(name) + 3), None)
        if hit is None:
            hit = next((i for i in body if lines[i][0] == f"- {name}:"), None)
        if name in values:
            if hit is None:
                hit = after + 1
                lines.insert(hit, [f"- {name}: {values[name]}", eol])
                end += 1
            else:
                lines[hit][0] = f"- {name}: {values[name]}"
        if hit is not None:
            after = max(after, hit)
    return "".join(l + s for l, s in lines) + text[cut:]


class _Lock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE:
                        self.path.unlink(missing_ok=True)  # a writer that died; the lock is older than any write takes
                        continue
                except OSError:
                    pass
                if time.monotonic() > end:
                    raise WatchError("the logbook is busy (another writer holds its lock). Try again in a moment.")
                time.sleep(0.05)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _digest(b):
    return hashlib.sha256(b).hexdigest()


def append(lb: Path, project: str, summary: str, tool: str, day=None, today=None, cancelled=None, commit_gate=None,
           committed=None, done=None, next_=None):
    """Log one watch; with done/next_, also set Done, Next and Updated in ## Now in the same write (§8.3).
    Returns "logged" or "already" (an identical entry exists: nothing is written, Now included).
    Raises WatchError on refusal."""
    day = check_input(summary, tool, day, today or date.today())
    fields = {name: clean_now(value, name.lower()) for name, value in (("Done", done), ("Next", next_))
              if value is not None}
    if cancelled and cancelled.is_set():
        raise WatchError("the call timed out before anything was written.")
    with _Lock(lb / ".waypoint.lock"):
        if cancelled and cancelled.is_set():
            raise WatchError("the call timed out before anything was written.")
        # Look the project up under the lock: on Windows a concurrent writer's os.replace can briefly hide its file.
        live = {p["name"]: p for p in logbook.load_projects(lb) if not p["archived"]}
        if not isinstance(project, str) or project not in live:
            raise WatchError(f"unknown project. Tracked projects: {', '.join(sorted(live)) or '(none)'}.")
        projects_dir = (lb / "projects").resolve()
        target = (lb / live[project]["file"]).resolve()
        if target.parent != projects_dir or target.name.startswith(("_", ".")):
            raise WatchError("that project's file isn't a plain file in projects/; nothing was written.")
        replace_busy = False
        for attempt in range(5):
            if cancelled and cancelled.is_set():
                raise WatchError("the call timed out before anything was written.")
            try:
                raw = target.read_bytes()
            except FileNotFoundError:
                raise WatchError("that project was just moved or removed; run take_fix to see current projects") from None
            new = insert_entry(raw.decode("utf-8"), day, tool, summary)
            if new is None:
                return "already"
            if fields:
                new = set_now(new, day, fields)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".watch.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(new.encode("utf-8"))
                if _digest(target.read_bytes()) != _digest(raw):
                    continue  # changed under us (e.g. an agent's direct edit): start over from the new content
                denied = False
                with commit_gate or nullcontext():
                    if cancelled and cancelled.is_set():
                        raise WatchError("the call timed out before anything was written.")
                    try:
                        os.replace(tmp, target)
                    except PermissionError:
                        replace_busy = True
                        denied = True
                    else:
                        if committed:
                            committed.set()
                if denied:
                    if attempt < 4:
                        time.sleep(0.2)
                        continue
                    break
                return "logged"
            finally:
                Path(tmp).unlink(missing_ok=True)
    if replace_busy:
        raise WatchError("the project file is busy (another process may have it open). Try again in a moment.")
    raise WatchError("the file kept changing while logging; try again in a moment.")
