"""Decide, by parsing (not pattern-matching), whether a shell command the agent wants to run is:

  read    - only looks at things: runs without asking (ls, cat, git status, du | sort ...)
  run     - anything else: the user approves it on a card that shows the exact command and warnings
  refuse  - never runs: sudo, the keychain tool, Diatonic's own data (keys, tokens), whole-disk wipes

A command is read-only only if every part of it is a known read-only program with harmless arguments, joined at most
by pipes. Anything the parser can't vouch for (chains, redirects, command substitution, unknown programs) is `run`.
"""
from __future__ import annotations

import os
import re
import shlex

READ_PROGRAMS = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag", "find", "fd", "pwd", "which", "whereis", "type",
    "file", "stat", "du", "df", "ps", "uptime", "whoami", "id", "date", "cal", "echo", "tree", "uname", "sw_vers", "hostname",
    "basename", "dirname", "realpath", "readlink", "sort", "uniq", "cut", "tr", "column", "jq", "nl", "diff", "cmp", "md5",
    "shasum", "sha256sum", "md5sum", "lsof", "vm_stat", "sysctl", "system_profiler", "mdfind", "mdls", "less", "more", "bat",
    "git", "brew", "npm", "pip", "pip3", "uv", "node", "python", "python3", "ruby", "go", "cargo", "rustc", "java", "docker",
    "ollama", "top", "free", "printenv", "env", "history", "man", "tldr",
}
# Programs that are read-only only with certain subcommands / flags.
GIT_READ = {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "describe", "tag", "shortlog",
            "config", "stash", "reflog", "grep", "whatchanged", "worktree", "ls-remote"}
PKG_READ = {"brew": {"list", "info", "search", "outdated", "deps", "leaves", "config", "--version", "doctor"},
            "npm": {"ls", "list", "view", "info", "outdated", "--version", "-v", "root", "prefix"},
            "pip": {"list", "show", "freeze", "--version", "check"}, "pip3": {"list", "show", "freeze", "--version", "check"},
            "uv": {"--version", "tree"}, "docker": {"ps", "images", "version", "info", "logs", "inspect", "stats"},
            "ollama": {"list", "ps", "show", "--version", "-v"}, "cargo": {"--version", "tree", "metadata"}}
VERSION_ONLY = {"node", "python", "python3", "ruby", "go", "rustc", "java"}     # read-only only as `<prog> --version`
SHELL_META = {";", "&&", "||", "&", ">", ">>", "<", "<<", "<<<", ">|", "&>", "(", ")", "|&"}
REFUSE_PROGRAMS = {"sudo", "su", "doas", "security", "launchctl", "csrutil", "diskutil", "mkfs", "dd", "shutdown", "reboot", "halt"}
SECRET_PATHS = re.compile(r"(^|/)(\.ssh|\.aws|\.gnupg|\.netrc|\.env(\.\w+)?|id_rsa\w*|id_ed25519\w*|\.npmrc|\.pypirc|credentials(\.json)?|.*\.pem|.*\.key)(/|$)")

WARN = [  # (regex on the raw command, warning shown on the approval card)
    (r"\brm\b.*\s-\w*[rRf]", "deletes files (recursive or forced)"),
    (r"\b(curl|wget|ssh|scp|rsync|sftp|ftp|nc|telnet)\b", "uses the network"),
    (r"\b(pip3?|npm|pnpm|yarn|brew|uv|cargo|gem|apt|apt-get|port)\s+(install|add|upgrade|update|uninstall|remove)\b", "installs or removes software (network)"),
    (r"\bgit\s+(push|pull|fetch|clone)\b", "uses the network (git)"),
    (r"\|\s*(sh|bash|zsh|python3?|node|ruby|perl)\b", "runs code piped in from another command"),
    (r"\b(chmod|chown|chgrp|xattr)\b", "changes permissions"),
    (r"(^|[^>])>(?!&)", "overwrites a file"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|checkout\s+--\s|restore\b)", "discards changes"),
    (r"\b(kill|killall|pkill)\b", "stops processes"),
    (r"\bmv\b", "moves or renames files"),
    (r"\b(osascript|open)\b", "controls other apps"),
]


def _tokens(cmd: str) -> list[str] | None:
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()")
        lex.whitespace_split = True
        return list(lex)
    except ValueError:            # unbalanced quotes
        return None


CHAIN = {"|", "&&", "||", ";"}   # joining read-only commands this way stays read-only


def _segments(tokens: list[str]) -> list[list[str]]:
    segs, cur = [], []
    for t in tokens:
        if t in CHAIN:
            segs.append(cur); cur = []
        else:
            cur.append(t)
    segs.append(cur)
    return segs


def _read_only_segment(seg: list[str]) -> bool:
    if not seg:
        return False
    prog = os.path.basename(seg[0])
    args = seg[1:]
    if prog not in READ_PROGRAMS:
        return False
    if prog in VERSION_ONLY:
        return args in (["--version"], ["-V"], ["-v"], ["version"])
    if prog in ("env", "hostname"):          # `env CMD` runs CMD; `hostname NAME` renames the machine
        return not args
    if prog == "printenv":
        return all(re.fullmatch(r"[A-Za-z_]\w*", a) for a in args)
    if prog == "sysctl":
        return not any(a == "-w" or "=" in a for a in args)
    if prog == "uv" and args[:1] == ["pip"]:
        return len(args) >= 2 and args[1] in ("list", "show", "freeze", "tree")
    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")), None)
        if sub not in GIT_READ or any(a.startswith("--output") for a in args):
            return False
        bad = {"branch": {"-d", "-D", "-m", "-M", "--delete", "--move", "-c", "-C"}, "remote": {"add", "remove", "rm", "rename", "set-url", "prune"},
               "tag": {"-d", "--delete", "-a", "-s", "-f"}, "config": {"--add", "--unset", "--replace-all", "--edit", "-e"},
               "stash": {"push", "pop", "apply", "drop", "clear", "save", "store", "create"}, "worktree": {"add", "remove", "prune", "move"}}
        if sub in bad and any(a in bad[sub] for a in args):
            return False
        if sub == "stash" and not (len(args) >= 2 and args[1] in ("list", "show")):
            return False
        if sub == "config" and not any(a in ("--get", "--list", "-l", "--get-all") for a in args):
            return False
        if sub in ("tag", "branch") and len([a for a in args if not a.startswith("-")]) > 1 and not {"-l", "--list"} & set(args):
            return False                       # `git tag v1` / `git branch new` create one
        return True
    if prog in PKG_READ:
        first = next((a for a in args if not a.startswith("-")), args[0] if args else None)
        return first in PKG_READ[prog] or (args and args[0] in PKG_READ[prog])
    if prog in ("find", "fd") and any(a in ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls", "-x", "--exec", "-X", "--exec-batch") for a in args):
        return False
    if prog == "sort" and any(a == "-o" or a.startswith("--output") for a in args):
        return False
    if prog == "tree" and "-o" in args:
        return False
    if prog in ("top",) and not ("-l" in args):    # interactive top would never end
        return False
    if prog in ("less", "more", "man", "history"):  # these need a terminal; harmless but useless here
        return False
    return True


def classify(cmd: str) -> dict:
    """{"kind": "read"|"run"|"refuse", "reason": str, "warnings": [str]}"""
    raw = (cmd or "").strip()
    m = re.fullmatch(r"cd\s+(\"[^\"]*\"|'[^']*'|[^\s;&|<>$`]+)\s*&&\s*(.+)", raw, re.S)
    if m:   # `cd somewhere && ls`: as safe as the part after the cd
        return classify(m.group(2))
    if not raw:
        return {"kind": "refuse", "reason": "There is no command.", "warnings": []}
    if len(raw) > 4000:
        return {"kind": "refuse", "reason": "That command is too long.", "warnings": []}
    if re.search(r"(^|[\s/'\"=])(~/|\$HOME/|/Users/[^/\s]+/)?\.diatonic\b", raw):
        return {"kind": "refuse", "reason": "Diatonic's own data (keys, tokens, settings) is off limits to the agent.", "warnings": []}
    if re.search(r":\s*\(\s*\)\s*\{", raw):
        return {"kind": "refuse", "reason": "That looks like a fork bomb.", "warnings": []}
    if re.search(r"\brm\s+(-\w+\s+)*(/|~|\$HOME|/Users|/Users/[^/\s]+)/?\s*($|\s)", raw) or re.search(r"\brm\s+(-\w+\s+)*/\*", raw):
        return {"kind": "refuse", "reason": "That would delete your whole home folder or disk.", "warnings": []}
    tokens = _tokens(raw)
    progs = set()
    if tokens:
        for seg in _segments([t for t in tokens if t not in SHELL_META]):
            if seg:
                progs.add(os.path.basename(seg[0]))
        # programs after ; && || too
        for i, t in enumerate(tokens):
            if t in SHELL_META and i + 1 < len(tokens):
                progs.add(os.path.basename(tokens[i + 1]))
    if progs & REFUSE_PROGRAMS or re.search(r"\b(sudo|doas)\b", raw):
        p = sorted(progs & REFUSE_PROGRAMS) or ["sudo"]
        return {"kind": "refuse", "reason": f"{p[0]} isn't allowed for the agent" + (" (it would wait for a password nobody can type)." if p[0] in ("sudo", "su", "doas") else "."), "warnings": []}
    raw_full, raw = raw, re.sub(r"\s*(?:[12]?>|&>)\s*/dev/null|\s*2>&1", "", raw)   # silencing output is harmless
    warnings = [w for rx, w in WARN if re.search(rx, raw)]
    tokens = _tokens(raw)
    secret = [t for t in (tokens or []) if SECRET_PATHS.search(t.rstrip("/"))]
    if secret:
        warnings.append(f"reads or touches secret files ({', '.join(sorted(set(secret))[:3])})")
    read = (tokens is not None and not secret
            and not any(t in SHELL_META and t not in CHAIN for t in tokens)
            and not any(x in raw for x in ("$(", "`", "${", "<(", ">(", "\n"))
            and all(_read_only_segment(s) for s in _segments(tokens)))
    if read:
        return {"kind": "read", "reason": "only reads", "warnings": []}
    return {"kind": "run", "reason": "needs approval", "warnings": list(dict.fromkeys(warnings))}


def _norm(cmd: str) -> list[str] | None:
    t = _tokens(cmd.strip())
    return [x for x in t] if t else None


def covered(cmd: str, allowed: list[str]) -> bool:
    """Is this command pre-approved for a scheduled job? Either it's listed exactly, or it is a chain (`&&`, `;`, `||`)
    whose every part is listed or read-only (`cd somewhere` counts as read-only). Pipes and redirects inside a part must
    match a listed entry exactly, so nothing new can be smuggled in."""
    raw = (cmd or "").strip()
    if not raw or classify(raw)["kind"] == "refuse":
        return False
    wanted = [n for n in (_norm(a) for a in allowed) if n]
    tokens = _norm(raw.replace("\n", " ; "))
    if tokens is None:
        return False
    if tokens in wanted:
        return True
    parts, cur = [], []
    for t in tokens:
        if t in ("&&", ";", "||"):
            parts.append(cur); cur = []
        else:
            cur.append(t)
    parts.append(cur)
    moved = False   # an unlisted `cd`: from then on relative paths point somewhere the user never approved
    for p in parts:
        if not p:
            continue
        if p in wanted:
            if moved and not (p[0] == "cd"):
                return False
            if p[0] == "cd":
                moved = False
            continue
        if p[0] == "cd" and len(p) <= 2:
            moved = True
            continue
        if any(t in SHELL_META for t in p) or classify(" ".join(shlex.quote(t) for t in p))["kind"] != "read":
            return False
    return True
