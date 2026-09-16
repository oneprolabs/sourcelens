"""Bounded source citations for tool-loop answers."""

from lensnode.consulted_sources import (
    MAX_CONSULTED_SOURCES,
    ConsultedSources,
)


def _command(tmp_path):
    return {
        "target_dirs": [
            {"name": "hosted_demo", "path": str(tmp_path / "sources" / "hosted_demo")},
        ],
    }


def _mount(tmp_path, relative, text):
    path = tmp_path / "sources" / "hosted_demo" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_read_window_becomes_a_relative_public_citation(tmp_path):
    _mount(tmp_path, "README.md", "# Demo\nsecret\n")
    sources = ConsultedSources(_command(tmp_path))

    sources.record_read(
        tmp_path / "sources" / "hosted_demo" / "README.md",
        1,
        2,
        "# Demo\nsecret",
    )

    citations = sources.citations("en-US")
    assert len(citations) == 1
    citation = citations[0]
    assert citation["path"] == "hosted_demo/README.md"
    assert not citation["path"].startswith("/")
    assert citation["start_line"] == 1
    assert citation["end_line"] == 2
    assert citation["source"] == "# Demo\nsecret"
    assert citation["supports"] == "Read while answering"
    assert citation["revision"]
    assert citation["id"].isalnum() or "-" in citation["id"]


def test_paths_outside_the_mounted_dirs_are_dropped(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    sources = ConsultedSources(_command(tmp_path))

    sources.record_read(outside, 1, 1, "secret = 1")

    assert sources.citations() == []


def test_search_hits_keep_their_line_window_and_query(tmp_path):
    _mount(tmp_path, "notes.md", "one\ntwo\nthree\n")
    sources = ConsultedSources(_command(tmp_path))

    sources.record_search(
        "two",
        [
            {
                "path": str(tmp_path / "sources" / "hosted_demo" / "notes.md"),
                "line": 2,
                "text": "two",
                "before": ["one"],
                "after": ["three"],
            },
        ],
    )

    citations = sources.citations("Simplified Chinese")
    assert citations[0]["path"] == "hosted_demo/notes.md"
    assert (citations[0]["start_line"], citations[0]["end_line"]) == (1, 3)
    assert citations[0]["source"] == "one\ntwo\nthree"
    assert citations[0]["supports"] == "为“two”检索到"


def test_label_language_accepts_codes_and_names(tmp_path):
    _mount(tmp_path, "notes.md", "one\n")

    for language, expected in (
        ("zh-CN", "回答过程中查阅"),
        ("Simplified Chinese", "回答过程中查阅"),
        ("es", "Leído al responder"),
        ("Spanish", "Leído al responder"),
        ("English", "Read while answering"),
    ):
        sources = ConsultedSources(_command(tmp_path))
        sources.record_read(
            tmp_path / "sources" / "hosted_demo" / "notes.md", 1, 1, "one"
        )
        assert sources.citations(language)[0]["supports"] == expected


def test_duplicates_collapse_and_the_result_stays_bounded(tmp_path):
    sources = ConsultedSources(_command(tmp_path))
    for index in range(MAX_CONSULTED_SOURCES + 3):
        sources.record_read(
            tmp_path / "sources" / "hosted_demo" / f"doc{index}.md",
            1,
            1,
            "line",
        )
    sources.record_read(
        tmp_path / "sources" / "hosted_demo" / "doc0.md", 1, 1, "line"
    )

    citations = sources.citations()
    assert len(citations) == MAX_CONSULTED_SOURCES
    assert len({citation["path"] for citation in citations}) == MAX_CONSULTED_SOURCES


def test_generated_mount_names_fall_back_to_the_datasource_name(tmp_path):
    mount = tmp_path / "sources" / "ds_9a7e7d95e01343a88ec12dcdc6a60a6b"
    mount.mkdir(parents=True)
    (mount / "notes.md").write_text("one\n", encoding="utf-8")
    command = {
        "target_dirs": [{"name": mount.name, "path": str(mount)}],
        "datasource_snapshots": [
            {"mount_name": mount.name, "datasource_name": "对对对"},
        ],
    }
    sources = ConsultedSources(command)

    sources.record_read(mount / "notes.md", 1, 1, "one")

    assert sources.citations()[0]["path"] == "对对对/notes.md"


def test_item_scoped_generated_mounts_use_the_datasource_name(tmp_path):
    mount = tmp_path / "sources" / "ds_9a7e7d95e01343a88ec12dcdc6a60a6b_1f2e3d4c"
    mount.mkdir(parents=True)
    (mount / "notes.md").write_text("one\n", encoding="utf-8")
    command = {
        "target_dirs": [{"name": mount.name, "path": str(mount)}],
        "datasource_snapshots": [
            {"mount_name": mount.name, "datasource_name": "对对对"},
        ],
    }
    sources = ConsultedSources(command)

    sources.record_read(mount / "notes.md", 1, 1, "one")

    assert sources.citations()[0]["path"] == "对对对/notes.md"


def test_readable_mount_names_are_kept(tmp_path):
    mount = tmp_path / "sources" / "hosted_demo"
    mount.mkdir(parents=True)
    (mount / "notes.md").write_text("one\n", encoding="utf-8")
    command = {
        "target_dirs": [{"name": "hosted_demo", "path": str(mount)}],
        "datasource_snapshots": [
            {"mount_name": "hosted_demo", "datasource_name": "托管工作区测试"},
        ],
    }
    sources = ConsultedSources(command)

    sources.record_read(mount / "notes.md", 1, 1, "one")

    assert sources.citations()[0]["path"] == "hosted_demo/notes.md"


def test_repeated_datasource_names_get_a_stable_index(tmp_path):
    """Two mounts of one datasource stay distinguishable and readable."""

    command = {"target_dirs": [], "datasource_snapshots": []}
    mounts = (
        f"ds_{'a' * 32}_11111111",
        f"ds_{'a' * 32}_22222222",
    )
    for mount in mounts:
        path = tmp_path / "sources" / mount
        path.mkdir(parents=True)
        (path / "notes.md").write_text("one\n", encoding="utf-8")
        command["target_dirs"].append({"name": mount, "path": str(path)})
        command["datasource_snapshots"].append(
            {"mount_name": mount, "datasource_name": "同名数据源"}
        )
    sources = ConsultedSources(command)

    sources.record_read(
        tmp_path / "sources" / mounts[0] / "notes.md", 1, 1, "one"
    )

    assert sources.citations()[0]["path"] == "同名数据源-1/notes.md"


def test_citation_paths_never_expose_a_generated_mount(tmp_path):
    """The backend strip is a safety net, not the normal path."""

    mount = tmp_path / "sources" / f"ds_{'b' * 32}"
    mount.mkdir(parents=True)
    (mount / "notes.md").write_text("one\n", encoding="utf-8")
    command = {
        "target_dirs": [{"name": mount.name, "path": str(mount)}],
        "datasource_snapshots": [
            {"mount_name": mount.name, "datasource_name": "托管工作区测试"},
        ],
    }
    sources = ConsultedSources(command)

    sources.record_read(mount / "notes.md", 1, 1, "one")

    path = sources.citations()[0]["path"]
    assert not path.startswith("ds_")
    assert path == "托管工作区测试/notes.md"


def test_citations_describe_consultation_not_support(tmp_path):
    """The label must not claim the source backs the answer."""

    _mount(tmp_path, "README.md", "demo\n")
    sources = ConsultedSources(_command(tmp_path))
    sources.record_read(
        tmp_path / "sources" / "hosted_demo" / "README.md", 1, 1, "demo"
    )

    citation = sources.citations("en-US")[0]
    assert citation["supports"] == "Read while answering"
    assert "support" not in citation["supports"].lower()


def test_source_line_count_must_cover_the_declared_window(tmp_path):
    _mount(tmp_path, "README.md", "# Demo\n")
    sources = ConsultedSources(_command(tmp_path))

    sources.record_read(
        tmp_path / "sources" / "hosted_demo" / "README.md",
        1,
        5,
        "# Demo",
    )

    assert sources.citations() == []


def test_consulted_sources_round_trip_through_checkpoint_state(tmp_path):
    _mount(tmp_path, "README.md", "one\ntwo\n")
    command = _command(tmp_path)
    sources = ConsultedSources(command)
    sources.record_read(
        tmp_path / "sources" / "hosted_demo" / "README.md", 1, 2,
        "one\ntwo",
    )

    restored = ConsultedSources(command)
    restored.restore_state(sources.export_state())

    assert restored.citations("en-US") == sources.citations("en-US")
