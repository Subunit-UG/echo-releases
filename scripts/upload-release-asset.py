#!/usr/bin/env python3
"""Upload one asset to an exact numeric GitHub release ID."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository")
    parser.add_argument("release_id")
    parser.add_argument("asset_name")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        parser.error("GH_TOKEN is required")
    if REPOSITORY.fullmatch(args.repository) is None:
        parser.error("repository must be OWNER/REPO")
    if not args.release_id.isdigit() or int(args.release_id) < 1:
        parser.error("release_id must be a positive integer")
    if Path(args.asset_name).name != args.asset_name or not args.asset_name.isprintable():
        parser.error("asset_name must be one printable basename")
    try:
        payload = args.path.read_bytes()
    except OSError as error:
        parser.error(str(error))
    if not payload:
        parser.error("asset file must not be empty")

    url = (
        f"https://uploads.github.com/repos/{args.repository}/releases/"
        f"{args.release_id}/assets?name={quote(args.asset_name, safe='')}"
    )
    content_type = {
        ".json": "application/json",
        ".dmg": "application/x-apple-diskimage",
    }.get(Path(args.asset_name).suffix.lower(), "application/octet-stream")
    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    except HTTPError as error:
        print(f"error: GitHub asset upload returned HTTP {error.code}", file=sys.stderr)
        return 1
    except (URLError, OSError, json.JSONDecodeError) as error:
        print(f"error: GitHub asset upload failed: {error}", file=sys.stderr)
        return 1
    asset_id = result.get("id")
    if (
        result.get("name") != args.asset_name
        or not isinstance(asset_id, int)
        or isinstance(asset_id, bool)
        or asset_id < 1
    ):
        print("error: GitHub returned an unexpected release asset", file=sys.stderr)
        return 1
    print(json.dumps({"id": asset_id, "name": result["name"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
