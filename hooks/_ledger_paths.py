"""Shared path resolution for the ledger hooks.

Two different questions, and conflating them is what broke the plugin port:

  WHERE IS THE CODE?      the plugin directory, wherever Claude Code installed
                          it -- typically ~/.claude/plugins/, outside any repo.
  WHERE IS THE PROJECT?   the repo the user is working in, which is where
                          ledgers live.

The pre-plugin version answered both with `__file__.parents[2]`, which only
worked because the hooks sat inside the repo they served. As a plugin that
resolves to the plugin's own directory, so every hook would quietly look for
ledgers in the wrong place and find none -- and since these hooks swallow
exceptions by design, that failure is indistinguishable from "nothing to do".

So: the code is found from ${CLAUDE_PLUGIN_ROOT}, the project from
${CLAUDE_PROJECT_DIR}, and each has a fallback chain for non-plugin installs.
"""
import os
import pathlib


def _git_root(start):
    p = pathlib.Path(start).resolve()
    for c in [p, *p.parents]:
        if (c / ".git").exists():
            return c
    return None


def project_root(payload=None):
    """The repo whose ledgers we manage.

    $LEDGER_ROOT wins, then the project dir Claude Code exports, then the cwd
    the hook payload reports, then the git root above the cwd, then the cwd.
    """
    env = os.environ.get("LEDGER_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return pathlib.Path(env)
    if payload and payload.get("cwd"):
        return _git_root(payload["cwd"]) or pathlib.Path(payload["cwd"])
    return _git_root(pathlib.Path.cwd()) or pathlib.Path.cwd()


# Kept for callers that resolve before reading the payload. Prefer
# project_root(payload) inside a hook -- it can use the reported cwd.
ROOT = project_root()

# Ordered by likelihood. $LEDGER_PY overrides everything.
_CANDIDATES = [
    "scripts/ledger.py",                        # this plugin's own layout
    ".claude/skills/ledger/scripts/ledger.py",  # skill install
    "scripts/ledger/ledger.py",                 # the original layout
]


def script():
    """Absolute path to ledger.py, or None if it cannot be found."""
    env = os.environ.get("LEDGER_PY")
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env)
    bases = []
    plugin = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin:
        bases.append(pathlib.Path(plugin))
    # The plugin root is normally two levels above this file (hooks/_x.py), so
    # derive it too -- the env var is absent when a hook is run by hand.
    bases.append(pathlib.Path(__file__).resolve().parents[1])
    bases.append(ROOT)
    for base in bases:
        for rel in _CANDIDATES:
            p = base / rel
            if p.exists():
                return p
    return None


def module():
    """Import ledger.py as a module, for callers that want its layout vars."""
    import importlib.util
    p = script()
    if not p:
        return None
    spec = importlib.util.spec_from_file_location("ledger", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ledgers(name="ledger.md", root=None):
    """Every ledger (or sibling file) on disk, under the configured layout.

    `root` overrides the project dir, so a hook that read a payload can pass
    the cwd it was given rather than re-deriving it.
    """
    mod = module()
    if not mod:
        return []
    base = mod.PRDS
    if root and pathlib.Path(root) != mod.ROOT:
        # ledger.py resolved its layout against a different root; re-anchor.
        base = pathlib.Path(str(mod.PRDS).replace(str(mod.ROOT), str(root), 1))
    pattern = mod.GLOB.replace("ledger.md", name)
    return sorted(base.glob(pattern)) if base.exists() else []
