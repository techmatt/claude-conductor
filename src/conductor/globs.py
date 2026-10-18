"""`paths:` globs: what an item may change, and whether two items' globs can overlap.

A glob is `/`-separated and relative to the repo root. `*`, `?` and `[...]` match
within one segment; `**` matches any number of segments. A glob also covers everything
under a directory it matches, so `docs` and `docs/**` mean the same. Matching ignores
case, so on Windows `Docs/x` and `docs/x` count as the same file.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

WILD = set("*?[")


def _segs(text: str) -> list[str]:
    return [s for s in text.replace("\\", "/").lower().split("/") if s not in ("", ".")]


def _wild(seg: str) -> bool:
    return any(c in WILD for c in seg)


def invalid(glob: str) -> bool:
    text = glob.replace("\\", "/")
    return not _segs(text) or text.startswith("/") or ":" in text or ".." in text.split("/")


def matches(path: str, glob: str) -> bool:
    """Does `glob` cover the repo-relative file `path`?"""
    return _covers(_segs(path), _segs(glob))


def _covers(p: list[str], g: list[str]) -> bool:
    if not g:
        return True                      # the glob matched a directory holding this path
    if g[0] == "**":
        return any(_covers(p[i:], g[1:]) for i in range(len(p) + 1))
    return bool(p) and fnmatchcase(p[0], g[0]) and _covers(p[1:], g[1:])


def outside(paths: list[str], globs: list[str]) -> list[str]:
    return [p for p in paths if not any(matches(p, g) for g in globs)]


def may_overlap(a: str, b: str) -> bool:
    """Conservative: False only when no path can match both globs."""
    ga, gb = _segs(a), _segs(b)
    for x, y in zip(ga, gb):
        if x == "**" or y == "**":
            return True
        if not _wild(x) and not _wild(y):
            if x != y:
                return False
        elif not _wild(x):
            if not fnmatchcase(x, y):
                return False
        elif not _wild(y):
            if not fnmatchcase(y, x):
                return False
        # Both wildcard segments: assume they can meet and keep comparing.
    return True                          # one is a prefix of the other: the shorter covers the longer


def only_prose(glob: str, prose: list[str]) -> bool:
    """Does `glob` name nothing but prose: a prose glob itself, or a literal path one covers?"""
    segs = _segs(glob)
    return any(segs == _segs(p) for p in prose) or (
        not any(_wild(s) for s in segs) and any(_covers(segs, _segs(p)) for p in prose))


def disjoint(a: list[str], b: list[str], prose: list[str] = ()) -> bool:
    """Two declared `paths` lists are disjoint only if every pair of globs is. Globs that
    name only `prose` files never count: those files merge textually."""
    if not a or not b:
        return False
    a, b = [g for g in a if not only_prose(g, prose)], [g for g in b if not only_prose(g, prose)]
    return not any(may_overlap(x, y) for x in a for y in b)
