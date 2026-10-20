"""The report Skill reuses its actual HTML asset and preserves authored content."""

import importlib.util
import json
import re
import subprocess
import sys
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2] / "skills" / "engineering-report"
SPEC = importlib.util.spec_from_file_location("report_renderer", SKILL / "scripts" / "render_report.py")
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def report():
    """Supply model-authored content rather than inferred or canned conclusions."""

    return {
        "title": "Selected period",
        "coverage": "Jira complete; GitHub has a recorded gap",
        "people": [
            {
                "name": 'Alice <script>alert("x")</script>',
                "completed": [{"text": "Resolved SL-1 (Jira)", "url": "https://jira.example/SL-1"}],
                "in_progress": ["Reviewing SL-2"],
                "risks": "Validation has not been observed",
                "comment": "The available source supports the listed events",
                "observed_completion": True,
            },
            {
                "name": "李四",
                "completed": [],
                "in_progress": ["Investigating SL-3"],
                "risks": "Event actor remains unknown",
                "comment": "Insufficient evidence to assess completion",
                "observed_completion": False,
            },
        ],
    }


class Elements(HTMLParser):
    """Collect real DOM attributes from the generated artifact."""

    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def base_css(markup):
    """Compare the reusable stylesheet without per-person filter rules."""

    css = re.search(r"<style>(.*?)</style>", markup, re.DOTALL).group(1)
    css = re.sub(r"^.*#f-(?:<person-slug-\d+>|person-\d+):checked.*\n?", "", css, flags=re.MULTILINE)
    return re.sub(r"<!--.*?-->", "", css, flags=re.DOTALL).strip()


def test_renderer_preserves_template_css_cards_filter_controls_links_and_escaping():
    data = report()
    original = deepcopy(data)
    markup = renderer.render(data)
    template = re.search(r"```html\s*\n(.*?)\n```", (SKILL / "OUTPUT_FORMAT.md").read_text(), re.DOTALL).group(1)
    assert base_css(markup) == base_css(template)
    parser = Elements()
    parser.feed(markup)
    cards = [attrs for tag, attrs in parser.elements if tag == "article"]
    radios = [attrs for tag, attrs in parser.elements if tag == "input"]
    labels = [attrs for tag, attrs in parser.elements if tag == "label"]
    assert len(cards) == 2
    assert {item["data-name"] for item in cards} == {"person-1", "person-2"}
    assert {item["id"] for item in radios} == {item["for"] for item in labels} == {"f-all", "f-person-1", "f-person-2"}
    assert not any(tag == "script" for tag, _ in parser.elements)
    assert any(tag == "a" and attrs["href"] == "https://jira.example/SL-1" for tag, attrs in parser.elements)
    assert "李四" in markup
    assert "Validation has not been observed" in markup
    assert "Insufficient evidence to assess completion" in markup
    assert data == original


@pytest.mark.parametrize("invalid", ["javascript:alert(1)", "data:text/html,test", "/relative-path"])
def test_source_link_cannot_inject_executable_content(invalid):
    data = report()
    data["people"][0]["completed"][0]["url"] = invalid
    with pytest.raises(ValueError, match="HTTP"):
        renderer.render(data)


def test_renderer_retains_all_people_and_uses_changed_business_content():
    data = report()
    data["people"] = [{**data["people"][1], "name": f"Account {index}"} for index in range(80)]
    first = renderer.render(data)
    data["people"][79]["comment"] = "A revised conclusion from additional evidence"
    second = renderer.render(data)
    assert second.count('<article class="card') == 80
    assert "A revised conclusion from additional evidence" in second
    assert first != second
    assert base_css(first) == base_css(second)


def test_skill_script_writes_final_html_through_the_existing_delivery_directory(tmp_path):
    output = tmp_path / "report.html"
    result = subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "render_report.py"), "--output", str(output)],
        input=json.dumps(report()),
        text=True,
        capture_output=True,
        env={"SOURCELENS_DELIVERABLE_DIR": str(tmp_path)},
        check=True,
    )
    assert json.loads(result.stdout)["ok"] is True
    assert output.read_text() == renderer.render(report())
