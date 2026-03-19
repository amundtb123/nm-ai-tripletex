"""Decode uploaded base64 files to disk and return metadata."""

from __future__ import annotations

import base64
import binascii
import logging
import os
import uuid
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

_DEFAULT_UPLOAD_ROOT = Path("/tmp/uploads")
UPLOAD_ROOT = Path(os.environ.get("AI_AGENT_UPLOAD_ROOT", str(_DEFAULT_UPLOAD_ROOT)))


class FileMeta(TypedDict):
    original_filename: str
    saved_path: str
    size_bytes: int


class FileDecodeError(Exception):
    """Raised when base64 content cannot be decoded or writing fails."""


def _ensure_within_upload_root(path: Path, root: Path) -> None:
    """Reject paths that resolve outside the upload root (defence in depth)."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FileDecodeError("Uploaded file path escaped upload directory") from exc


def decode_files_to_tmp(
    files: list[dict[str, str]],
    tmp_root: Path | None = None,
) -> list[FileMeta]:
    root = (tmp_root or UPLOAD_ROOT).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        logger.exception("Could not ensure upload root %s", root)
        raise FileDecodeError(f"Cannot create or access upload directory: {root}") from exc

    results: list[FileMeta] = []
    run_id = uuid.uuid4().hex[:12]
    batch_dir = root / f"batch_{run_id}"

    try:
        batch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise FileDecodeError(f"Cannot create batch directory under {root}") from exc

    for idx, item in enumerate(files):
        filename = item.get("filename") or f"upload_{idx}.bin"
        b64 = item.get("content_base64")
        if not b64:
            raise FileDecodeError(f"Missing content_base64 for file index {idx} ({filename})")

        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            safe_name = f"upload_{idx}.bin"

        dest_path = (batch_dir / safe_name).resolve()
        _ensure_within_upload_root(dest_path, root)

        try:
            raw = base64.b64decode(b64, validate=False)
        except binascii.Error as exc:
            raise FileDecodeError(f"Invalid base64 for {safe_name}") from exc

        try:
            dest_path.write_bytes(raw)
        except OSError as exc:
            raise FileDecodeError(f"Failed to write {dest_path}") from exc

        _ensure_within_upload_root(dest_path, root)

        results.append(
            FileMeta(
                original_filename=filename,
                saved_path=str(dest_path),
                size_bytes=len(raw),
            )
        )
        logger.debug("Saved upload size=%d bytes (path not logged at INFO)", len(raw))

    return results
