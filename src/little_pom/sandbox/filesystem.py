"""Descriptor-relative, no-follow filesystem operations shared with the container agent."""
from __future__ import annotations

import hashlib
import os
from pathlib import PurePosixPath
import stat


def parts_for(path: str, *, allow_root: bool = False) -> tuple[str, ...]:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise ValueError("Invalid workspace path")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError("Path must remain relative to the workspace")
    if not parsed.parts and not allow_root:
        raise ValueError("A file path is required")
    # Hidden benchmark material is never a tool input, even in a synthetic fixture.
    forbidden = {"tests", "solution", "authoring", "secrets", ".git", "ground_truth.json", "generate_data.py"}
    public_lean_input = parsed.parts in {("Tests",), ("Tests", "Public.lean")}
    if not public_lean_input and any(part.lower() in forbidden for part in parsed.parts):
        raise ValueError("Private input path is forbidden")
    return parsed.parts


def open_parent(root: str, parts: tuple[str, ...], *, create: bool = False) -> int:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_bytes(root: str, path: str, limit: int) -> bytes:
    parts = parts_for(path)
    parent = open_parent(root, parts[:-1])
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("Only single-link regular files are readable")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(fd, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise ValueError("Source exceeds the configured evidence byte limit")
            return data
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def write_bytes(root: str, path: str, data: bytes) -> None:
    parts = parts_for(path)
    parent = open_parent(root, parts[:-1], create=True)
    try:
        # O_EXCL temporary + rename avoids truncating existing hardlinks outside the boundary.
        import uuid
        temporary = f".little-{uuid.uuid4().hex}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            try:
                target = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISREG(target.st_mode):
                    raise ValueError("Write target must be a regular file")
            except FileNotFoundError:
                pass
            os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
    finally:
        os.close(parent)


def list_files(root: str, path: str, limit: int) -> list[str]:
    parts = parts_for(path, allow_root=True)
    fd = open_parent(root, parts)
    try:
        names = []
        with os.scandir(fd) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                try:
                    parts_for(str(PurePosixPath(path) / entry.name))
                except ValueError:
                    continue
                names.append(entry.name)
                if len(names) > limit:
                    raise ValueError("Directory exceeds listing limit")
        return sorted(names)
    finally:
        os.close(fd)


def artifact(path: str, data: bytes, *, kind: str) -> dict:
    return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "kind": kind}
