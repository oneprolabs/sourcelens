"""Regression coverage for immutable datasource version storage."""

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from lens.datasource.versions import record_datasource_versions
from lens.models import DataSource


class DatasourceStorageTests(TestCase):
    """A resync must preserve the files earlier versions point to."""

    def test_versions_survive_resync(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.settings(MEDIA_ROOT=str(root / 'media')):
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
                    .read_text(),
                    'SourceLens version one',
                )
                self.assertFalse((live / 'versions').exists())
