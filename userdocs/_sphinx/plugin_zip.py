"""Zip the wfc plugin for the docs site, as a chat's "upload a plugin" page takes it.

claude.ai (Customize > Plugins > Upload a plugin) and ChatGPT (Plugins > Add > Upload
plugin archive) accept one archive with the plugin's manifest at its root. ``conf.py``
calls :func:`build_plugin_zip` on every docs build and serves the result at the site
root, so each version of the docs links the plugin of the same version.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

# A fixed time for every entry, so the same plugin always zips to the same bytes.
_FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def build_plugin_zip(plugin_dir: Path, out: Path) -> Path:
    """Write every file under *plugin_dir* into *out*, paths relative to it, with "/"."""
    plugin_dir = Path(plugin_dir)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in plugin_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(plugin_dir).as_posix(), _FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return out
