"""Path normalization and folder mapping (LOGBOOK-SPEC §4.2)."""
import functools
import os
import re

_WINDOWS_LIKE = re.compile(r"^([a-zA-Z]:|\\\\)")
_WINDOWS_ABS = re.compile(r"^([a-zA-Z]:[\\/]|\\\\)")  # drive-relative "D:app" is not absolute


def norm(p):
    """Comparable form: no \\\\?\\ prefix, one slash style, no trailing separator, case-folded on Windows."""
    p = (p or "").strip()
    if p[:8].upper() == "\\\\?\\UNC\\":
        p = "\\\\" + p[8:]  # \\?\UNC\server\share -> \\server\share
    elif p.startswith("\\\\?\\"):
        p = p[4:]
    windows = os.name == "nt" or bool(_WINDOWS_LIKE.match(p))
    if windows:
        p = p.replace("/", "\\")
        p = p.rstrip("\\") if not re.fullmatch(r"[a-zA-Z]:\\?", p) else p[:2] + "\\"
        return p.lower()
    return p.rstrip("/") or "/"


def _sep(p):
    return "\\" if "\\" in p or re.match(r"^[a-z]:", p) else "/"


def map_folder(cwd, roots):
    """Map a working directory to the top-level folder under the longest matching root, or None."""
    c = norm(cwd)
    if not c:
        return None
    best = None
    for root in roots:
        r = norm(root)
        sep = _sep(r)
        prefix = r if r.endswith(sep) else r + sep
        if c.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    if best is None:
        return None
    first = c[len(best):].split(_sep(best))[0]
    return best + first if first else None


@functools.lru_cache(maxsize=4096)
def real(p):
    """norm() of the path with symlinks followed (§4.2 v1.1). Relative or empty paths stay unresolved."""
    p = (p or "").strip()
    if not p:
        return ""
    stripped = norm(p)  # long-path prefix gone first: on POSIX, realpath would read a prefixed path as relative
    # resolve native absolute paths only: a Windows path seen on POSIX (or drive-relative "D:app") stays as written
    if not (os.path.isabs(stripped) and _WINDOWS_ABS.match(stripped) if os.name == "nt" else stripped.startswith("/")):
        return stripped
    try:
        return norm(os.path.realpath(stripped))
    except (OSError, ValueError):
        return stripped


def placer(projects, roots):
    """cwd -> folder key: the tracked project whose Path equals cwd or is a separator-bounded ancestor
    (longest wins), else the top-level folder under the longest root (§4.2 v1.1). `projects` is an ordered
    list of Path strings; for duplicate Paths the first one wins."""
    keys = []
    for p in projects:
        k = real(p)
        if k and k not in keys:
            keys.append(k)
    keys.sort(key=len, reverse=True)
    roots = [real(r) for r in roots]  # resolved like cwds, so 8.3 short names and symlinked roots still match

    def place(cwd):
        c = real(cwd)
        if not c:
            return None
        for k in keys:
            if c == k or c.startswith(k if k.endswith(_sep(k)) else k + _sep(k)):
                return k
        return map_folder(c, roots)
    return place
