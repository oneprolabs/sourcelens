"""Regression coverage for immutable datasource and session storage."""

import json
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase

from lens.datasource.versions import record_datasource_versions
from lens.models import DataSource
from lens.datasource.workspace import (
    build_session_workspace, cleanup_session_workspace, session_source_dirs,
)


class DatasourceStorageTests(TestCase):
    """A resync must preserve files used by existing sessions."""

    def test_versions_survive_resync_and_workspace_rebuild(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.settings(
                MEDIA_ROOT=str(root / 'media'),
                LENS_SESSION_WORKSPACE_ROOT=str(root / 'sessions'),
            ):
                source = DataSource.objects.create(
                    name='SourceLens', source_type='git'
                )
                item = source.items.create(
                    name='SourceLens', source_type='git',
                    storage_key=f'datasources/{source.uuid}/items/repo',
                )
                live = root / 'media' / item.storage_key
                live.mkdir(parents=True)
                (live / 'README.md').write_text('SourceLens version one')
                first = record_datasource_versions(source)[0]
                (live / 'README.md').write_text('SourceLens version two')
                second = record_datasource_versions(source)[0]
                self.assertNotEqual(first.storage_key, second.storage_key)
                self.assertEqual(
                    (root / 'media' / first.storage_key / 'README.md')
                    .read_text(), 'SourceLens version one',
                )
                self.assertFalse((live / 'versions').exists())
                snapshots = Mock()
                snapshots.select_related.return_value = [SimpleNamespace(
                    datasource=source, item=item, mount_name='sourcelens',
                    storage_key=first.storage_key,
                    datasource_version=first.version,
                )]
                snapshots.all.return_value = snapshots.select_related.return_value
                session = SimpleNamespace(
                    uuid=uuid.uuid4(), datasource_snapshots=snapshots,
                )
                workspace = build_session_workspace(session)
                manifest = json.loads((workspace / 'manifest.json').read_text())
                self.assertEqual(len(manifest['items']), 1)
                build_session_workspace(session)
                self.assertEqual(
                    json.loads((workspace / 'manifest.json').read_text()),
                    manifest,
                )
                link = workspace / 'sources/sourcelens'
                self.assertTrue(link.is_symlink())
                self.assertEqual(
                    link.resolve(), root / 'media' / first.storage_key
                )
                self.assertEqual(session_source_dirs(session), [
                    {'path': str(link), 'name': 'sourcelens'}
                ])
                self.assertEqual(
                    (workspace / 'sources/sourcelens/README.md').read_text(),
                    'SourceLens version one',
                )
                self.assertTrue(cleanup_session_workspace(session.uuid))
                self.assertTrue((root / 'media' / first.storage_key).is_dir())
