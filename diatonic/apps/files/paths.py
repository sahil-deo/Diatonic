"""The file sandbox. Every path the agent or the viewer touches goes through `Sandbox.resolve`.

Rules (all enforced on the symlink-resolved real path, so links can't escape a root):
  * the path must be inside one of the folders the user added;
  * credentials and key material are refused even inside a root (unless the user opts in);
  * roots themselves must not be system folders or hold Diatonic's own secrets.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from ...config import home

DENY_DIR_NAMES = {".ssh", ".gnupg", ".aws", ".kube", ".docker", "Keychains", ".password-store"}
DENY_FILE_NAMES = {".env", ".netrc", ".npmrc", ".pypirc", ".git-credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
                   "secrets.json", "credentials", "credentials.json", "service-account.json", "authorized_keys"}
DENY_PATTERNS = (".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keychain*", "*.kdbx", "*.gpg", "id_rsa*", "*secret*.json")
ALLOWED_ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".trash", ".Trash", ".next", ".cache",
             "dist", "build", "target", ".pytest_cache", ".mypy_cache", ".tox", ".gradle", "Pods"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
# macOS aliases /etc, /var and /tmp to /private/..., so compare real paths. Broad folders are refused as roots themselves;
# system trees are refused with everything inside them.
FORBIDDEN_EXACT = {os.path.realpath(x) for x in ("/", "/private", "/var", "/tmp", "/Volumes", "/opt", "/Users", "/home")}
FORBIDDEN_TREES = [os.path.realpath(x) for x in ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/dev", "/Applications", "/cores")]


class PathError(Exception):
    """A refusal with a message safe to show to the model and the user."""


def _real(p: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(p))))


def validate_root(path: str) -> Path:
    if "\0" in path:
        raise PathError("Invalid path.")
    p = _real(path.strip())
    if not p.exists() or not p.is_dir():
        raise PathError(f"“{path}” isn't an existing folder.")
    if p == _real("~"):
        raise PathError("Your whole home folder is too broad to share. Pick a subfolder such as Documents or a project.")
    if str(p) in FORBIDDEN_EXACT or p == Path.home().resolve().parent or any(str(p) == t or str(p).startswith(t + "/") for t in FORBIDDEN_TREES):
        raise PathError(f"{p} is too broad or a system folder, so it can't be shared. Pick a project or documents folder.")
    h = _real(home())
    for guarded in (h, _real("~/.ssh"), _real("~/.gnupg"), _real("~/.aws"), _real("~/Library")):
        if p == guarded or guarded in p.parents:
            raise PathError(f"{p} holds sensitive data and can't be shared.")
    if p in _real("~").parents:
        raise PathError("That folder is too broad to share.")
    return p


def is_denied(real: Path) -> str | None:
    """A reason string if this path is credentials/key material, else None."""
    if any(part in DENY_DIR_NAMES for part in real.parts):
        return "it is inside a credentials folder"
    name = real.name
    if name in ALLOWED_ENV_EXAMPLES:
        return None
    if name in DENY_FILE_NAMES or any(fnmatch.fnmatch(name.lower(), pat) for pat in DENY_PATTERNS):
        return "it looks like credentials or key material"
    h = _real(home())
    if real == h or h in real.parents:
        return "it belongs to Diatonic itself"
    return None


class Sandbox:
    def __init__(self, roots: list[str], allow_sensitive: bool = False):
        self.roots: list[Path] = []
        for r in roots:
            try:
                self.roots.append(validate_root(r))
            except PathError:
                continue  # a root that vanished or became invalid is simply not shared
        self.allow_sensitive = allow_sensitive

    def root_of(self, real: Path) -> Path | None:
        return next((r for r in self.roots if real == r or r in real.parents), None)

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        if not self.roots:
            raise PathError("No folder is shared yet. Add one in the Files panel.")
        if not isinstance(path, str) or "\0" in path or not path.strip():
            raise PathError("That path isn't valid.")
        p = path.strip().strip("`\"'")
        if p.startswith("@"):       # the model often copies the "@file" token from the chat verbatim
            p = p[1:].strip("\"'")
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.join(self.roots[0], p)          # relative paths live in the first shared folder
        real = _real(p)
        if self.root_of(real) is None:
            raise PathError(f"{path} is outside the shared folders ({', '.join(str(r) for r in self.roots)}).")
        why = None if self.allow_sensitive else is_denied(real)
        if why:
            raise PathError(f"{real.name} is blocked because {why}.")
        if must_exist and not real.exists():
            raise PathError(f"{path} doesn't exist.")
        return real

    def display(self, real: Path) -> str:
        """Path as shown to the model: relative to its root when there is one root, else prefixed by the root name."""
        root = self.root_of(real)
        if root is None:
            return str(real)
        rel = real.relative_to(root).as_posix()
        return rel if len(self.roots) == 1 else f"{root.name}/{rel}".rstrip("/")

    @staticmethod
    def skipped(name: str, is_dir: bool) -> bool:
        return (name in SKIP_DIRS) if is_dir else (name in SKIP_FILES)
