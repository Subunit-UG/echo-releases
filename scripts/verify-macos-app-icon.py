#!/usr/bin/env python3
"""Verify that a built macOS app carries the system app icon its source declares.

Since macOS 26 an app icon compiled from Icon Composer ships as
``Contents/Resources/Assets.car`` and is selected through ``CFBundleIconName``.
Tauri copies an ``.car`` entry from ``bundle.icon`` (or compiles an ``.icon``
entry with actool 26 or newer). An older toolchain skips this step with only a
log line, and the app silently falls back to the legacy ``.icns`` icon. This
check turns that silent fallback into a failed build.

Sources without an ``.car``/``.icon`` entry in ``bundle.icon`` are reported and
accepted unchanged, so older source tags keep building.

usage: verify-macos-app-icon.py TAURI_CONF APP_BUNDLE [--assetutil PATH]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


class IconError(ValueError):
    """The built app does not carry the declared system icon."""


def declared_system_icon(conf_path: Path) -> str | None:
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    icons = (conf.get("bundle") or {}).get("icon") or []
    if not isinstance(icons, list):
        raise IconError("bundle.icon must be a list")
    for entry in icons:
        if isinstance(entry, str) and entry.rstrip("/").endswith((".car", ".icon")):
            return entry
    return None


def icon_names_in_car(car: Path, assetutil: str) -> set[str]:
    tool = shutil.which(assetutil)
    if tool is None:
        raise IconError(f"assetutil not found: {assetutil}")
    result = subprocess.run([tool, "--info", str(car)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise IconError("assetutil could not read Assets.car")
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise IconError("assetutil returned no JSON") from error
    return {
        entry.get("Name")
        for entry in entries
        if isinstance(entry, dict) and entry.get("AssetType") == "Icon Image" and entry.get("Name")
    }


def verify(conf_path: Path, app: Path, assetutil: str = "assetutil") -> str:
    declared = declared_system_icon(conf_path)
    if declared is None:
        return "no system icon declared in bundle.icon; legacy icon only"
    resources = app / "Contents" / "Resources"
    car = resources / "Assets.car"
    if not car.is_file() or car.stat().st_size == 0:
        raise IconError(f"{declared} is declared, but the app has no Contents/Resources/Assets.car")
    info_path = app / "Contents" / "Info.plist"
    if not info_path.is_file():
        raise IconError("app has no Info.plist")
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    name = info.get("CFBundleIconName")
    if not isinstance(name, str) or not name:
        raise IconError("Info.plist has no CFBundleIconName; macOS would ignore Assets.car")
    fallback = info.get("CFBundleIconFile")
    if not isinstance(fallback, str) or not fallback:
        raise IconError("Info.plist has no CFBundleIconFile fallback for older macOS")
    fallback_file = resources / (fallback if fallback.endswith(".icns") else f"{fallback}.icns")
    if not fallback_file.is_file():
        raise IconError(f"CFBundleIconFile points to a missing file: {fallback_file.name}")
    names = icon_names_in_car(car, assetutil)
    if name not in names:
        raise IconError(f"Assets.car has no app icon named {name!r} (found: {sorted(names)})")
    return f"system icon verified: Assets.car with app icon {name!r}, fallback {fallback_file.name}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("conf", type=Path)
    parser.add_argument("app", type=Path)
    parser.add_argument("--assetutil", default="assetutil")
    args = parser.parse_args()
    try:
        print(verify(args.conf, args.app, args.assetutil))
    except (IconError, OSError, json.JSONDecodeError, plistlib.InvalidFileException) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
