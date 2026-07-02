"""Phase 86: personal preferences persisted in the USER settings file.

A tiny, shared read/write for ``~/.d2c/settings.yaml`` sections of the shape::

    ui:
      default: textual
    model:
      default: deepseek-v4-pro
    thinking:
      default: medium

These are PERSONAL (user-scope only) — a project/managed settings file can't set
them. Writes preserve unrelated keys and are atomic. Reads never raise: a
missing/unreadable/invalid file just means no preference.

The UI preference (Phase 79/80) and the model/thinking preferences (Phase 86)
all go through here; ``d2c.tui`` re-exports thin ``user_ui_pref``/
``set_user_ui_pref`` wrappers for backward compatibility.
"""

from __future__ import annotations

import os

import yaml


def get_user_pref(section: str) -> str | None:
    """The persisted ``<section>.default`` string, or None if unset/invalid."""
    from d2c.settings import user_settings_path

    path = user_settings_path()
    try:
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    sec = data.get(section) if isinstance(data, dict) else None
    value = sec.get("default") if isinstance(sec, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def set_user_pref(section: str, value: str | None) -> None:
    """Persist ``<section>.default = value`` (or remove it when value is None /
    ``"auto"``). Preserves other keys; atomic write."""
    from d2c.settings import user_settings_path

    path = user_settings_path()
    data: object = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}

    sec = data.get(section)
    if not isinstance(sec, dict):
        sec = {}
    if value is None or value == "auto":
        sec.pop("default", None)
    else:
        sec["default"] = value
    if sec:
        data[section] = sec
    else:
        data.pop(section, None)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
