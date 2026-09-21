"""Reader-facing citation path and payload tests."""

from django.test import SimpleTestCase

from lens.citations import public_run_citations, sanitize_run_citations

DATASOURCE_MOUNT = "ds_2f1c0a9b8d7e6f5a4b3c2d1e0f9a8b7c"


def _citation(**overrides):
    """Return one valid citation snapshot for sanitizer tests."""

    value = {
        "id": "src-1",
        "project": "workspace",
        "repository": "workspace",
        "revision": "working-tree",
        "path": "src/app.py",
        "symbol": "",
        "start_line": 3,
        "end_line": 4,
        "supports": "Read while answering",
        "source": "line three\nline four",
    }
    value.update(overrides)
    return value


class CitationPathTest(SimpleTestCase):
    """Keep the internal datasource mount layer out of citation paths."""

    def test_drops_datasource_mount_segment(self):
        output = sanitize_run_citations([
            _citation(path=f"{DATASOURCE_MOUNT}/src/app.py"),
        ])

        self.assertEqual(output[0]["path"], "src/app.py")

    def test_drops_item_scoped_datasource_mount_segment(self):
        output = sanitize_run_citations([
            _citation(
                path=f"{DATASOURCE_MOUNT}_0f9a8b7c/README.md",
            ),
        ])

        self.assertEqual(output[0]["path"], "README.md")

    def test_keeps_paths_without_a_datasource_mount(self):
        output = sanitize_run_citations([
            _citation(path="backend/lens/services.py"),
            _citation(id="src-2", path="ds_notes/readme.md"),
        ])

        self.assertEqual(
            [item["path"] for item in output],
            ["backend/lens/services.py", "ds_notes/readme.md"],
        )

    def test_keeps_bare_mount_path_unchanged(self):
        output = sanitize_run_citations([
            _citation(path=DATASOURCE_MOUNT),
        ])

        self.assertEqual(output[0]["path"], DATASOURCE_MOUNT)

    def test_public_citations_expose_the_stripped_path(self):
        output = public_run_citations([
            _citation(path=f"{DATASOURCE_MOUNT}/docs/guide.md"),
        ])

        self.assertEqual(output[0]["path"], "docs/guide.md")
        self.assertNotIn("source", output[0])

    def test_collapses_repeated_windows_of_one_path(self):
        output = sanitize_run_citations([
            _citation(id="src-1", path="src/app.py"),
            _citation(
                id="src-2",
                path="src/app.py",
                start_line=10,
                end_line=11,
            ),
        ])

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["id"], "src-1")

    def test_keeps_every_distinct_path_without_a_fixed_cap(self):
        output = sanitize_run_citations([
            _citation(id=f"src-{index}", path=f"src/file{index}.py")
            for index in range(8)
        ])

        self.assertEqual(len(output), 8)
        self.assertEqual(
            len({item["path"] for item in output}),
            8,
        )
