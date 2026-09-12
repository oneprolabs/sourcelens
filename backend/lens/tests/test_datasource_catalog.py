"""Regression coverage for catalogs without a LensNode."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from lens.datasource.services import (
    DataSourcePathError,
    list_datasource_files,
)


class DatasourceCatalogTests(SimpleTestCase):
    """Exercise storage catalogs through the file-list service."""

    def setUp(self):
        """Create isolated storage and a datasource with no node."""

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        self.override = self.settings(MEDIA_ROOT=self.temp.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.items = []
        manager = Mock()
        manager.filter.return_value.order_by.return_value = self.items
        self.datasource = SimpleNamespace(lensnode_id=None, items=manager)

    def test_new_datasource_returns_empty_catalog(self):
        """An unsynced datasource needs neither a node nor a directory."""

        result = list_datasource_files(self.datasource)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["results"], [])

    def test_manifest_filtering_pagination_and_conversion(self):
        """Return actual stored paths and conversion metadata."""

        root = self.storage / "datasources/example/items/one"
        root.mkdir(parents=True)
        self.items.append(SimpleNamespace(
            uuid="one", storage_key="datasources/example/items/one"
        ))
        (root / "manifest.json").write_text(json.dumps({"items": [
            {"local_path": "b.pdf", "status": "skipped"},
            {"local_path": "a.pdf", "status": "synced"},
            {"local_path": "../secret.pdf"},
            {"local_path": "/secret.pdf"},
            {"local_path": "outside.pdf"},
        ]}))
        (root / "outside.pdf").symlink_to(self.storage / "secret.pdf")
        sidecar = root / "b.pdf.sourcelens"
        sidecar.mkdir()
        (sidecar / "meta.json").write_text(json.dumps({
            "conversion": {"status": "success", "generated_at": "today"}
        }))
        result = list_datasource_files(self.datasource, page=2, page_size=1)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["results"][0]["path"], "b.pdf")
        result = list_datasource_files(
            self.datasource, query="B.PDF", sync_status="synced",
            conversion_status="success",
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["converted_at"], "today")

    def test_storage_key_cannot_escape_storage(self):
        """Reject malformed keys instead of listing another directory."""

        self.items.append(SimpleNamespace(uuid="one", storage_key="../other"))
        with self.assertRaises(DataSourcePathError):
            list_datasource_files(self.datasource)
