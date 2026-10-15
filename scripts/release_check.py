"""Release gate: what ships must hold no local config, secrets, owner paths or logbook data (R0.5).

    python scripts/release_check.py dist [DIR]   check the wheel and sdist in DIR (default: build them in a temp folder)
    python scripts/release_check.py tree [DIR]   scan the public view of DIR's git-tracked files (default: this repo)
    python scripts/release_check.py export DEST  write the public tree to DEST (scanned; never pushed): a new folder
                                                 becomes a fresh repo; a clean clone of the public repo gets one commit

Owner-specific strings (a domain, a KV id, a username) are never committed: list them one per line (regular
expressions) in `.private-patterns` (git-ignored) or the PRIVATE_PATTERNS environment variable (a CI secret).
"""
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GENERIC = [
    r"[A-Za-z]:[\\/]+Users[\\/]+(?!you\b|me\b|<)[A-Za-z0-9._-]+",  # a real Windows profile path
    r"/home/(?!you\b|me\b|runner\b)[a-z][a-z0-9_-]+/",
    r"/Users/(?!you\b|me\b)[A-Za-z][A-Za-z0-9._-]+/",
    r"gh[opsu]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}",  # GitHub tokens
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]
# what the wheel and sdist may contain (sdist paths are relative to its top folder)
WHEEL_OK = re.compile(r"^(waypoint/.*|waypoint_mcp-[^/]+\.dist-info/.*)$")
SDIST_OK = re.compile(r"^(waypoint/.*|tests/test_[a-z_]+\.py|waypoint_mcp\.egg-info/.*|PKG-INFO|pyproject\.toml|setup\.cfg|README\.md|LICENSE)$")
NEVER = re.compile(r"(^|/)(projects/[^_]|DASHBOARD\.md$|dashboard\.json$|(?<!waypoint/templates/)chart\.html$|beacon/|docs/reviews/|wrangler\.local|\.dev\.vars|\.env)")
# files the public tree must never hold (the fictional example logbook is the one allowed logbook)
TREE_NEVER = re.compile(r"(^|/)(\.env(\..*)?|\.dev\.vars|wrangler\.local\.jsonc?|\.private-patterns|[^/]*\.(pem|key|p12)"
                        r"|projects/[^_/][^/]*\.md|projects/_archive/[^/]+\.md|DASHBOARD\.md|dashboard\.json|(?<!waypoint/templates/)chart\.html|\.waypoint\.lock)$")
EXAMPLE = "examples/logbook/"
# the maintainer's history (paths, reviews of private work): kept in the private repo, not shipped publicly
PRIVATE_ONLY = ("docs/reviews/", "docs/research/", "docs/PLAN.md", "docs/ui/HANDOFF-CHATGPT.md")


def patterns():
    extra = os.environ.get("PRIVATE_PATTERNS", "").splitlines()
    f = REPO / ".private-patterns"
    if f.exists():
        extra += f.read_text(encoding="utf-8").splitlines()
    extra = [p.strip() for p in extra if p.strip() and not p.lstrip().startswith("#")]
    return [re.compile(p, re.I) for p in GENERIC + extra], len(extra)


def scan(name, data: bytes, pats):
    text = data.decode("utf-8", "replace")
    return [f"{name}: matches a private pattern (#{i + 1})" for i, p in enumerate(pats) if p.search(text) or p.search(name)]


def check_dist(folder=None):
    """Inspect the archives in `folder` (the very files that get uploaded), or build them in a temp folder."""
    if folder is None:
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([sys.executable, "-m", "build", "--outdir", out, str(REPO)], check=True, capture_output=True)
            return check_dist(Path(out))
    pats, n = patterns()
    problems, archives = [], [f for f in Path(folder).iterdir() if f.suffix == ".whl" or f.name.endswith(".tar.gz")]
    if sorted(f.suffix for f in archives) != [".gz", ".whl"]:
        problems.append(f"expected one wheel and one sdist in {folder}, found {[f.name for f in archives]}")
    for f in archives:
        if f.suffix == ".whl":
            with zipfile.ZipFile(f) as z:
                members = [(m, z.read(m)) for m in z.namelist() if not m.endswith("/")]
            ok = WHEEL_OK
        else:
            with tarfile.open(f) as t:
                members = [(m.name.split("/", 1)[1], t.extractfile(m).read()) for m in t.getmembers() if m.isfile()]
            ok = SDIST_OK
        print(f"{f.name}: {len(members)} files")
        for name, data in members:
            print(f"  {name}")
            if not ok.match(name) or NEVER.search(name):
                problems.append(f"{f.name}: unexpected file {name}")
            problems += scan(f"{f.name}:{name}", data, pats)
    return problems, n


def check_names(names):
    return [f"{n}: this kind of file must never be public" for n in names
            if TREE_NEVER.search(n) and not n.startswith(EXAMPLE)]


def check_tree(root: Path):
    """The public view: every tracked file except PRIVATE_ONLY, by name and by content."""
    pats, n = patterns()
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout
    names = [x for x in out.decode("utf-8").split("\0") if x and not x.startswith(PRIVATE_ONLY)]
    problems = check_names(names)
    for name in names:
        p = root / name
        if p.is_file():
            problems += scan(name, p.read_bytes(), pats)
    return problems, n


def export(dest: Path):
    """The public tree: HEAD's tracked files minus PRIVATE_ONLY. A new DEST becomes a fresh one-commit repo; an
    existing clean clone of the public repo gets one new commit on top of its history. Never pushes."""
    git = lambda *a: subprocess.run(["git", "-C", str(dest), *a], check=True, capture_output=True, text=True)
    update = dest.exists()
    if update:
        if not (dest / ".git").exists() or git("status", "--porcelain").stdout.strip():
            sys.exit(f"{dest} exists but isn't a clean clone of the public repo; use a clean clone or a new folder")
        git("rm", "-rq", "--ignore-unmatch", ".")  # the public tree becomes exactly this export, deletions included
    names = subprocess.run(["git", "-C", str(REPO), "ls-tree", "-r", "-z", "--name-only", "HEAD"], check=True,
                           capture_output=True).stdout.decode("utf-8").split("\0")
    kept = [n for n in names if n and not n.startswith(PRIVATE_ONLY)]
    for n in kept:
        data = subprocess.run(["git", "-C", str(REPO), "show", f"HEAD:{n}"], check=True, capture_output=True).stdout
        (dest / n).parent.mkdir(parents=True, exist_ok=True)
        (dest / n).write_bytes(data)
    if not update:
        git("init", "-q", "-b", "main")
    git("add", "-A")
    if update and not git("status", "--porcelain").stdout.strip():
        print(f"{dest} already matches the export; nothing to commit.")
        return [], patterns()[1]
    version = re.search(r'^version = "([^"]+)"', (dest / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
    message = f"Waypoint {version}" + ("" if update else ": first public release")
    me = lambda k: subprocess.run(["git", "-C", str(REPO), "config", k], capture_output=True, text=True).stdout.strip()
    who = f"{me('user.name')} <{me('user.email')}>"  # the engine repo's identity becomes the public commit's author
    problems, n = check_tree(dest)
    problems += scan("commit identity", who.encode(), patterns()[0])
    if not problems:
        git("-c", f"user.name={me('user.name')}", "-c", f"user.email={me('user.email')}",
            "commit", "-q", "-m", message)
        meta = git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B").stdout
        problems += scan("commit metadata", meta.encode(), patterns()[0])
    if not problems:
        print(f"{'Updated' if update else 'Exported'} {len(kept)} files in {dest} as {who} (left out: {', '.join(PRIVATE_ONLY)}). Nothing was pushed.")
    return problems, n


def main(argv):
    if not argv or argv[0] not in ("dist", "tree", "export") or (argv[0] == "export" and len(argv) < 2):
        sys.exit(__doc__)
    arg = Path(argv[1]) if len(argv) > 1 else None
    if argv[0] == "export":
        problems, n = export(arg.resolve())
    else:
        problems, n = check_dist(arg) if argv[0] == "dist" else check_tree(arg or REPO)
    print(f"{len(GENERIC)} generic and {n} private patterns checked.")
    if problems:
        print("\n".join(problems))
        sys.exit(f"release check failed: {len(problems)} problem(s)")
    print("release check passed.")


if __name__ == "__main__":
    main(sys.argv[1:])
