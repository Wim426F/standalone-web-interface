#!/usr/bin/env python3
"""Release version numbers for the two update channels (used by build.yml).

Stable releases are vMAJOR.MINOR (minor + 1 per release from main). Dev
pre-releases are previews of the NEXT stable version: vMAJOR.MINOR-dev.N,
N counting up from 1 per planned stable version. So versions sort as

    v4.23 < v4.24-dev.1 < v4.24-dev.2 < v4.24 < v4.25-dev.1

which is also how the web UI compares them (data/app.js parseVer).

    python scripts/release_version.py stable            # version for a main release
    python scripts/release_version.py dev               # version for a dev pre-release
    python scripts/release_version.py previous v4.24    # tag the release notes start from
    gh release list --json tagName,isPrerelease,isDraft --limit 1000 \\
      | python scripts/release_version.py prune --keep v4.24-dev.3
                                                        # dev release pages to delete

Tags are read from `git tag` (stable/dev/previous) unless --tags is given.
A command that has nothing to release prints nothing.
"""
import argparse
import json
import re
import subprocess
import sys

STABLE = re.compile(r"v(\d+)\.(\d+)(?:\.(\d+))?")
DEV = re.compile(r"v(\d+)\.(\d+)(?:\.(\d+))?-dev\.([1-9]\d*)")
DEV_KEEP = 5  # dev pre-release pages kept by prune (their tags always stay)


def key(tag):
    """Sort key of a stable or dev tag, None for anything else. A stable
    release comes after its own dev previews."""
    m = STABLE.fullmatch(tag or "")
    if m:
        return (int(m[1]), int(m[2]), int(m[3] or 0), 1, 0)
    m = DEV.fullmatch(tag or "")
    if m:
        return (int(m[1]), int(m[2]), int(m[3] or 0), 0, int(m[4]))
    return None


def is_stable(tag):
    return STABLE.fullmatch(tag or "") is not None


def is_dev(tag):
    return DEV.fullmatch(tag or "") is not None


def next_stable(tags):
    """The next stable version: minor + 1 of the newest STABLE tag. Dev tags
    never count, so previews of v4.24 don't push the release to v4.25."""
    stable = sorted((t for t in tags if is_stable(t)), key=key)
    if not stable:
        return "v0.1"
    major, minor = key(stable[-1])[:2]
    return f"v{major}.{minor + 1}"


def stable_version(tags, head_tags):
    """Version for a release from main: a stable tag already on HEAD (a re-run,
    or a tag pushed by hand) wins; otherwise the next one. Dev tags on HEAD (a
    fast-forward merge of dev) are ignored."""
    on_head = sorted((t for t in head_tags if is_stable(t)), key=key)
    return on_head[-1] if on_head else next_stable(tags)


def dev_version(tags, head_tags):
    """Version for a dev pre-release: <next stable>-dev.<N+1>. A dev tag already
    on HEAD (a re-run) is reused. HEAD already carrying a stable tag (main
    merged back into dev) has nothing new to preview: returns ""."""
    if any(is_stable(t) for t in head_tags):
        return ""
    on_head = sorted((t for t in head_tags if is_dev(t)), key=key)
    if on_head:
        return on_head[-1]
    base = next_stable(tags)
    n = max((key(t)[4] for t in tags if is_dev(t) and t.startswith(base + "-dev.")), default=0)
    return f"{base}-dev.{n + 1}"


def previous(tags, version):
    """Where the generated release notes start: for a stable release the
    previous stable one (so its notes cover every dev preview since); for a
    dev release the newest release of either kind before it."""
    me = key(version)
    if me is None:
        return ""
    pool = [t for t in tags if key(t) is not None and key(t) < me]
    if is_stable(version):
        pool = [t for t in pool if is_stable(t)]
    return max(pool, key=key, default="")


def prune(releases, keep=(), count=DEV_KEEP):
    """Dev pre-release pages to delete, oldest first: all but the newest `count`
    (plus any tag in `keep`). Only dev-shaped, published pre-releases are ever
    candidates; stable releases, drafts and other tags are never touched."""
    dev = [r["tagName"] for r in releases
           if is_dev(r.get("tagName")) and r.get("isPrerelease") and not r.get("isDraft")]
    dev.sort(key=key, reverse=True)
    kept = set(dev[:count]) | set(keep)
    return sorted((t for t in dev if t not in kept), key=key)


def git_tags(*args):
    out = subprocess.run(["git", "tag", "--list", *args], check=True, capture_output=True, text=True).stdout
    return [t for t in out.split() if t]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=["stable", "dev", "previous", "prune"])
    p.add_argument("version", nargs="?", help="previous: the version being released")
    p.add_argument("--tags", help="comma-separated tags to use instead of git's")
    p.add_argument("--head-tags", help="comma-separated tags on HEAD instead of git's")
    p.add_argument("--keep", action="append", default=[], help="prune: a tag to keep as well")
    p.add_argument("--count", type=int, default=DEV_KEEP, help="prune: dev pages to keep")
    a = p.parse_args(argv)

    if a.command == "prune":
        out = prune(json.load(sys.stdin), keep=a.keep, count=a.count)
        print("\n".join(out))
        return 0
    split = lambda s: [t for t in s.split(",") if t]
    tags = split(a.tags) if a.tags is not None else git_tags()
    if a.command == "previous":
        if not a.version:
            p.error("previous needs the version being released")
        print(previous(tags, a.version))
        return 0
    head = split(a.head_tags) if a.head_tags is not None else git_tags("--points-at", "HEAD")
    print(stable_version(tags, head) if a.command == "stable" else dev_version(tags, head))
    return 0


if __name__ == "__main__":
    sys.exit(main())
