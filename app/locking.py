"""Small cross-process locks for the private cut state and site writer."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockBusyError(RuntimeError):
    """Another live cut owns the requested lock."""


@contextmanager
def directory_lock(
    root: Path,
    *,
    name: str,
    wait_seconds: float = 30.0,
    stale_seconds: float = 3_600.0,
) -> Iterator[None]:
    """Take an atomic directory lock with owner metadata and stale recovery."""

    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / name
    if lock_path.parent != root or lock_path.name != name:
        raise LockBusyError("ruta de bloqueo insegura")
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            lock_path.mkdir(mode=0o700)
        except FileExistsError:
            if _recover_stale_lock(lock_path, stale_seconds=stale_seconds):
                continue
            if time.monotonic() >= deadline:
                owner = _read_owner(lock_path)
                detail = f"; owner={owner}" if owner else ""
                raise LockBusyError(f"otro corte sigue en ejecución{detail}") from None
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            continue
        break
    try:
        _write_owner(lock_path)
        yield
    finally:
        _release(lock_path)


def private_state_lock(root: Path, *, wait_seconds: float = 30.0) -> Iterator[None]:
    return directory_lock(root, name=".radar-cut.lock", wait_seconds=wait_seconds)


def site_writer_lock(root: Path, *, wait_seconds: float = 30.0) -> Iterator[None]:
    return directory_lock(root, name=".radar-site.lock", wait_seconds=wait_seconds)


def _write_owner(lock_path: Path) -> None:
    owner = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": time.time(),
    }
    path = lock_path / "owner.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(owner, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())


def _read_owner(lock_path: Path) -> dict[str, object] | None:
    owner_path = lock_path / "owner.json"
    if owner_path.is_symlink():
        return None
    try:
        raw = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _recover_stale_lock(lock_path: Path, *, stale_seconds: float) -> bool:
    if lock_path.is_symlink() or not lock_path.is_dir():
        raise LockBusyError("el bloqueo existente no es un directorio seguro")
    owner = _read_owner(lock_path)
    age = _lock_age(lock_path, owner)
    if owner is not None and _owner_is_live(owner):
        return False
    if owner is not None and age < stale_seconds:
        return False
    if owner is None and age < stale_seconds:
        return False

    stale_path = lock_path.with_name(f"{lock_path.name}.stale-{os.getpid()}-{time.time_ns()}")
    try:
        os.replace(lock_path, stale_path)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise LockBusyError("no se pudo recuperar un bloqueo vencido") from exc
    try:
        shutil.rmtree(stale_path)
    except OSError as exc:
        raise LockBusyError("no se pudo limpiar un bloqueo vencido") from exc
    return True


def _lock_age(lock_path: Path, owner: dict[str, object] | None) -> float:
    started = owner.get("started_at") if owner else None
    if isinstance(started, (int, float)) and not isinstance(started, bool):
        return max(0.0, time.time() - float(started))
    try:
        return max(0.0, time.time() - lock_path.stat().st_mtime)
    except OSError:
        return float("inf")


def _owner_is_live(owner: dict[str, object]) -> bool:
    if owner.get("hostname") != socket.gethostname():
        return False
    pid = owner.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _release(lock_path: Path) -> None:
    try:
        if lock_path.is_symlink():
            raise LockBusyError("el bloqueo cambió inesperadamente a un enlace")
        if not lock_path.is_dir():
            raise LockBusyError("el bloqueo cambió inesperadamente de tipo")
        shutil.rmtree(lock_path)
    except FileNotFoundError:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Ejecuta un comando bajo el bloqueo de corte")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("se requiere un comando")
    try:
        with private_state_lock(args.state_dir, wait_seconds=args.wait_seconds):
            completed = subprocess.run(command, check=False)
    except LockBusyError as exc:
        print(f"Error de bloqueo: {exc}", file=sys.stderr)
        raise SystemExit(75) from exc
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
