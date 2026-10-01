"""Where the backend is and which keys to send.

Precedence: command-line flags > environment > config file > defaults.
  env:  WEBKIT_URL, WEBKIT_API_KEY, WEBKIT_ADMIN_KEY, WEBKIT_VNC_URL, WEBKIT_DOWNLOAD_DIR
        (Claude plugin userConfig arrives as CLAUDE_PLUGIN_OPTION_URL / _API_KEY / _ADMIN_KEY)
  file: ~/.config/web-kit/config.toml  (or $WEBKIT_CONFIG), keys: url, api_key, admin_key, vnc_url
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_URL = "http://localhost:8082"
KEYS = ("url", "api_key", "admin_key", "vnc_url")
_ENV = {
    "url": ("WEBKIT_URL", "CLAUDE_PLUGIN_OPTION_URL"),
    "api_key": ("WEBKIT_API_KEY", "CLAUDE_PLUGIN_OPTION_API_KEY"),
    "admin_key": ("WEBKIT_ADMIN_KEY", "CLAUDE_PLUGIN_OPTION_ADMIN_KEY"),
    "vnc_url": ("WEBKIT_VNC_URL",),
}


def config_path() -> Path:
    if os.environ.get("WEBKIT_CONFIG"):
        return Path(os.environ["WEBKIT_CONFIG"]).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "web-kit" / "config.toml"


def download_dir() -> Path:
    if os.environ.get("WEBKIT_DOWNLOAD_DIR"):
        return Path(os.environ["WEBKIT_DOWNLOAD_DIR"]).expanduser()
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "web-kit" / "downloads"


def read_file() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {k: str(v) for k, v in data.items() if k in KEYS and v}


def write_file(values: dict) -> Path:
    """Rewrite the config file (0600) with `values`."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{k} = "{_toml_escape(v)}"' for k, v in values.items() if k in KEYS and v]
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# web-kit CLI config (managed by `webkit config set`)\n" + "\n".join(lines) + "\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return path


def _toml_escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class Config:
    url: str = DEFAULT_URL
    api_key: str = ""
    admin_key: str = ""
    vnc_url: str = ""
    sources: dict = field(default_factory=dict)  # key -> "flag" | "env:NAME" | "file" | "default"

    @property
    def vnc(self) -> str:
        if self.vnc_url:
            return self.vnc_url
        u = urlparse(self.url)
        return f"{u.scheme}://{u.hostname}:6080/vnc.html"


def load(flags: dict | None = None) -> Config:
    flags = {k: v for k, v in (flags or {}).items() if v}
    file_values = read_file()
    cfg = Config()
    for key in KEYS:
        if key in flags:
            setattr(cfg, key, flags[key])
            cfg.sources[key] = "flag"
            continue
        env_name = next((n for n in _ENV[key] if os.environ.get(n)), None)
        if env_name:
            setattr(cfg, key, os.environ[env_name])
            cfg.sources[key] = f"env:{env_name}"
        elif key in file_values:
            setattr(cfg, key, file_values[key])
            cfg.sources[key] = "file"
        else:
            cfg.sources[key] = "default" if key == "url" else "unset"
    cfg.url = cfg.url.rstrip("/")
    return cfg


def mask(value: str) -> str:
    if not value:
        return "(unset)"
    return value[:4] + "…" + value[-2:] if len(value) > 10 else "***"
