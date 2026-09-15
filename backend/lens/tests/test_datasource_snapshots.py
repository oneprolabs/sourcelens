"""Regression coverage for Session datasource mount-name validation."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from lens.datasource.snapshots import (
    DatasourceSnapshotError,
    _validate_mount_names,
)


class MountNameValidationTests(SimpleTestCase):
    """A bad Session mount name must fail before the run is dispatched."""

    def test_valid_mount_names_pass(self):
        rows = [
            SimpleNamespace(mount_name="docs"),
            SimpleNamespace(mount_name="repo_1"),
            SimpleNamespace(mount_name="A-2_b"),
        ]
        _validate_mount_names(rows)

    def test_duplicate_mount_names_are_rejected(self):
        rows = [
            SimpleNamespace(mount_name="docs"),
            SimpleNamespace(mount_name="docs"),
        ]
        with self.assertRaises(DatasourceSnapshotError):
            _validate_mount_names(rows)

    def test_path_like_or_empty_mount_names_are_rejected(self):
        for name in ("", "../escape", "a/b", ".hidden", "with space"):
            with self.assertRaises(DatasourceSnapshotError):
                _validate_mount_names([SimpleNamespace(mount_name=name)])
