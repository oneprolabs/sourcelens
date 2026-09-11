"""Archive extraction must honor server-provided limits."""

import zipfile

import pytest

from lensnode.lensnode.datasource_sync import (
    DataSourceSyncError,
    _extract_zip_archive,
    _upload_extraction_limits,
)


def test_limit_defaults():
    """Invalid and missing limits retain compatibility defaults."""

    assert _upload_extraction_limits(None) == (104857600, 300)
    assert _upload_extraction_limits({
        "max_extracted_bytes": -1,
        "max_extracted_files": True,
    }) == (104857600, 300)


@pytest.mark.parametrize("limits,error", [
    ({"max_extracted_bytes": 3}, "DATASOURCE_UPLOAD_SIZE_LIMIT"),
    ({"max_extracted_files": 1}, "DATASOURCE_UPLOAD_FILE_LIMIT"),
])
def test_custom_limits(tmp_path, limits, error):
    """Smaller configured byte and count limits stop extraction."""

    package = tmp_path / "test.zip"
    root = tmp_path / "output"
    root.mkdir()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("a.txt", b"abcd")
        archive.writestr("b.txt", b"efgh")
    with pytest.raises(DataSourceSyncError, match=error):
        _extract_zip_archive(package, root, limits)


def test_exact_boundary(tmp_path):
    """The configured size and file-count boundary is inclusive."""

    package = tmp_path / "test.zip"
    root = tmp_path / "output"
    root.mkdir()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("a.txt", b"abcd")
    result = _extract_zip_archive(package, root, {
        "max_extracted_bytes": 4,
        "max_extracted_files": 1,
    })
    assert len(result) == 1
    assert result[0].read_bytes() == b"abcd"
