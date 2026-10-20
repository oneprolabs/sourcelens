"""Source-owned normalization preserves event meaning and coverage gaps."""

import json

import pytest

from lensnode.plugin_package_loader import load_plugin_support, load_runtime_contract

support = load_plugin_support("activity_evidence")


@pytest.mark.parametrize(
    "provider,kind,event,actor_field",
    [
        ("github", "issues", "closed", "closed_by"),
        ("gitlab", "merge_requests", "merged", "merged_by"),
        ("jira", "issues", "resolved", "resolved_by"),
    ],
)
def test_event_actor_is_distinct_from_author_and_current_assignee(provider, kind, event, actor_field):
    item = {
        "key": "SL-1",
        "number": 1,
        "author": {"username": "alice"},
        "assignee": "Bob",
        "assignee_identity": "bob",
        actor_field: {"username": "carol", "name": "Carol"},
        "updated_at": "2026-10-15T09:00:00Z",
        "updated": "2026-10-15T09:00:00Z",
        {"closed": "closed_at", "merged": "merged_at", "resolved": "resolution_date"}[event]: "2026-10-15T08:00:00Z",
    }
    record = support._record(provider, "conn", "repo", kind, item, "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z")
    assert record["event_actor"]["identity"] == "carol"
    assert record["event_actor"]["verified"] is True
    assert record["roles"]["author"]["identity"] == "alice"
    assert record["roles"]["assignee"]["identity"] == "bob"
    del item[actor_field]
    record = support._record(provider, "conn", "repo", kind, item, "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z")
    assert record["event_actor"] is None


def test_display_name_only_is_never_a_verified_actor():
    item = {
        "number": 1,
        "author": {"username": "alice", "name": "Alice"},
        "merged_by": {"name": "Carol"},
        "merged_at": "2026-10-15T08:00:00Z",
    }
    record = support._record(
        "gitlab", "conn", "repo", "merge_requests", item, "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z"
    )
    assert record["event_actor"]["verified"] is False
    assert record["roles"]["author"]["name"] == "Alice"


def test_update_actor_and_jira_creator_remain_distinct_from_current_assignee():
    item = {
        "key": "SL-1",
        "assignee": "Bob",
        "assignee_identity": "bob",
        "creator": "Alice",
        "creator_identity": "alice",
        "reporter": "Dan",
        "reporter_identity": "dan",
        "updated_by": {"accountId": "carol", "displayName": "Carol"},
        "updated": "2026-10-15T08:00:00Z",
    }
    record = support._record("jira", "conn", "repo", "issues", item, "2026-10-15T00:00:00Z", "2026-10-16T00:00:00Z")
    assert record["event_actor"]["identity"] == "carol"
    assert record["roles"]["creator"]["identity"] == "alice"
    assert record["roles"]["reporter"]["identity"] == "dan"
    assert record["person"] == "Bob"


def test_provider_projections_preserve_available_event_actors_without_inventing_missing_ones():
    github = load_runtime_contract("github", "1.0.0").execute_tool.__globals__
    gitlab = load_runtime_contract("gitlab", "1.0.0").execute_tool.__globals__
    jira = load_runtime_contract("jira", "1.0.0").execute_tool.__globals__
    assert github["_activity_issue_summary"]({"closed_by": {"login": "carol"}})["closed_by"] == "carol"
    assert github["_activity_pull_request_summary"]({"merged_by": {"login": "carol"}})["merged_by"] == "carol"
    assert gitlab["_activity_merge_request"]({"merge_user": {"username": "carol"}})["merged_by"]["username"] == "carol"
    assert gitlab["_activity_issue"]({"closed_by": {"username": "carol"}})["closed_by"]["username"] == "carol"
    projected = jira["_activity_issue"](
        {
            "key": "SL-1",
            "fields": {
                "creator": {"accountId": "alice-id", "displayName": "Alice"},
                "reporter": {"accountId": "bob-id", "displayName": "Bob"},
            },
        },
        "SL",
    )
    assert projected["creator_identity"] == "alice-id"
    assert projected["reporter_identity"] == "bob-id"
    assert not projected.get("updated_by")


@pytest.mark.parametrize(
    "provider,item",
    [
        ("jira", {"key": "SL-1", "status": "Done", "resolution_date": "2026-10-01T08:00:00Z"}),
        ("gitlab", {"number": 1, "state": "closed"}),
    ],
)
def test_current_done_or_closed_state_does_not_invent_a_completion_event(provider, item):
    record = support._record(
        provider,
        "conn",
        "repo",
        "issues",
        {**item, "updated_at": "2026-10-15T09:00:00Z", "updated": "2026-10-15T09:00:00Z"},
        "2026-10-15T00:00:00Z",
        "2026-10-16T00:00:00Z",
    )
    assert record["event"] == "updated"
    assert record["event_actor"] is None


@pytest.mark.parametrize("gap", [{"errors": ["discovery failed"]}, {"repositories_truncated": True}])
def test_empty_results_with_a_discovery_gap_are_incomplete(gap):
    result = support.activity_evidence("github", {"ok": True, "repositories": [], **gap})
    assert result["complete"] is False
    assert result["records"] == []


def test_exact_commits_merge_resources_but_provider_identities_remain_separate():
    commit = {
        "sha": "abc",
        "author_name": "Alice",
        "author_email": "alice@example.invalid",
        "committed_at": "2026-10-15T09:00:00Z",
    }
    source = {
        "ok": True,
        "since": "2026-10-15T00:00:00Z",
        "until": "2026-10-16T00:00:00Z",
        "repositories": [{"repository": repo, "commits": [commit]} for repo in ["one", "two"]],
    }
    github = support.activity_evidence("github", source, "conn")
    gitlab = support.activity_evidence("gitlab", source, "conn")
    assert len(github["records"]) == 1
    assert github["records"][0]["resources"] == ["one", "two"]
    assert github["records"][0]["person_id"] != gitlab["records"][0]["person_id"]


def test_canonical_projection_does_not_consume_the_raw_source_budget():
    contract = load_runtime_contract("jira", "1.0.0")
    source = {
        "ok": True,
        "since": "2026-10-15T00:00:00Z",
        "until": "2026-10-16T00:00:00Z",
        "items": [{"key": f"SL-{index}", "updated": "2026-10-15T09:00:00Z"} for index in range(40)],
    }
    budget = len(
        json.dumps(
            {**source, "_evidence": {"complete": True}}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    projected = contract.project_tool_result("jira_activity_summary", source, budget)
    evidence = contract.project_tool_evidence("jira_activity_summary", projected, "conn")
    assert projected["items"] == source["items"]
    assert "records" not in projected["_evidence"]
    assert len(evidence["records"]) == len(source["items"])
    assert evidence["complete"] is True
