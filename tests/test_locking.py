from __future__ import annotations

import pytest

from app.locking import LockBusyError, directory_lock


def test_directory_lock_refuses_a_second_live_writer(tmp_path) -> None:
    with (
        directory_lock(tmp_path, name=".cut.lock", wait_seconds=0),
        pytest.raises(LockBusyError, match="otro corte sigue"),
        directory_lock(tmp_path, name=".cut.lock", wait_seconds=0),
    ):
        pass


def test_directory_lock_rejects_a_symlinked_lock(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".cut.lock").symlink_to(outside, target_is_directory=True)

    with (
        pytest.raises(LockBusyError, match="seguro"),
        directory_lock(tmp_path, name=".cut.lock", wait_seconds=0),
    ):
        pass
