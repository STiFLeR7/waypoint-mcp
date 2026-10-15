"""Project bookkeeping the user asks for: archive, restore, remove, ignore (spec Part A, LOGBOOK-SPEC §1).
Never changes Status or log entries; every write holds the logbook lock."""
from contextlib import contextmanager
from pathlib import Path

from . import logbook
from .chart import _atomic_write
from .paths import real
from .watch import WatchError, _Lock


@contextmanager
def _locked(lb: Path):
    lock = _Lock(lb / ".waypoint.lock")
    try:
        lock.__enter__()
    except WatchError as e:
        raise ValueError(str(e).rstrip(".")) from None
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def find(lb: Path, name: str, archived: bool = False) -> dict:
    """The live (or archived) project called `name`, matched case-insensitively."""
    pool = [p for p in logbook.load_projects(lb) if p["archived"] == archived]
    for p in pool:
        if p["name"].casefold() == (name or "").casefold():
            return p
    kind = "archived project" if archived else "tracked project"
    raise ValueError(f"no {kind} is called {name!r}. {kind[0].upper() + kind[1:]}s: "
                     + (", ".join(sorted(p["name"] for p in pool)) or "none"))


def _taken(folder: Path, stem: str) -> bool:
    return any(f.is_file() and f.suffix.lower() == ".md" and f.stem.casefold() == stem.casefold()
               for f in folder.glob("*"))


def _move(lb: Path, name: str, archived: bool) -> Path:
    with _locked(lb):
        p = find(lb, name, archived=archived)
        src = lb / p["file"]
        to = lb / "projects" / ("" if archived else "_archive")
        if _taken(to, src.stem):
            where = "projects/" if archived else "projects/_archive/"
            raise ValueError(f"{where} already has a file called {src.stem}.md; rename one of them first")
        to.mkdir(exist_ok=True)
        dest = to / src.name
        src.rename(dest)
        return dest


def archive(lb: Path, name: str) -> Path:
    return _move(lb, name, archived=False)


def restore(lb: Path, name: str) -> Path:
    return _move(lb, name, archived=True)


def _add_ignore(lb: Path, folder: Path) -> bool:
    """Add folder to projects/_ignore.txt unless an equal entry is there. Caller holds the lock."""
    f = lb / "projects" / "_ignore.txt"
    if real(str(folder)) in logbook.load_ignore(lb):
        return False
    text = f.read_bytes().decode("utf-8") if f.exists() else ""  # bytes: keep the file's own line endings
    eol = "\r\n" if "\r\n" in text else "\n"
    _atomic_write(f, text + ("" if not text or text.endswith("\n") else eol) + f"{folder}{eol}")
    return True


def remove(lb: Path, name: str, ignore: bool = False) -> dict:
    """Stop tracking: delete the live project file (git keeps history). ignore: also list its folder in _ignore.txt."""
    with _locked(lb):
        p = find(lb, name)
        f = lb / p["file"]
        f.unlink()  # first: if the delete fails (file open elsewhere), nothing else has changed
        ignored = None
        if ignore and p["path"]:
            folder = p["path"]
            try:
                folder = str(Path(folder).expanduser().resolve())
                _add_ignore(lb, Path(folder))
            except (OSError, UnicodeError, RuntimeError):  # the file is already gone: say so, and how to finish
                raise ValueError(f"removed {p['file']}, but couldn't add {folder} to projects/_ignore.txt; "
                                 f'run: waypoint ignore "{folder}"') from None
            ignored = folder
        return {"file": f, "logs": len(p["logs"]), "ignored": ignored}


def ignore_problem(lb: Path, folder) -> str | None:
    """Why `folder` can't be ignored (a live project tracks it), or None."""
    folder = Path(folder).expanduser().resolve()
    for p in logbook.load_projects(lb):
        if not p["archived"] and p["path"] and real(p["path"]) == real(str(folder)):
            return f"{folder} is tracked by {p['file']}; remove or archive it first"
    return None


def ignore(lb: Path, folder, undo: bool = False) -> bool:
    """List folder in projects/_ignore.txt (not a project; hide it from Untracked), or drop it with undo."""
    folder = Path(folder).expanduser().resolve()
    key = real(str(folder))
    with _locked(lb):
        if not undo:
            if msg := ignore_problem(lb, folder):
                raise ValueError(msg)
            return _add_ignore(lb, folder)
        f = lb / "projects" / "_ignore.txt"
        if not f.exists():
            return False
        lines = f.read_bytes().decode("utf-8").splitlines(keepends=True)
        keep = [l for l in lines if l.strip().startswith("#") or not l.strip() or real(l.strip()) != key]
        if len(keep) == len(lines):
            return False
        _atomic_write(f, "".join(keep))
        return True
