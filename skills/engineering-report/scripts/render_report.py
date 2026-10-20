"""Fill this Skill's HTML skeleton with model-authored report content."""

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


def text(value):
    """Escape a required text slot without interpreting its business meaning."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Report text slots require nonempty strings")
    return html.escape(value)


def items(values):
    """Render supplied facts with optional source links and no inferred conclusions."""

    if not isinstance(values, list):
        raise ValueError("Report item slots require lists")
    output = []
    for value in values:
        if isinstance(value, str):
            content = text(value)
        elif isinstance(value, dict):
            content = text(value.get("text"))
            url = value.get("url")
            if url is not None:
                if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
                    raise ValueError("Source links must use HTTP or HTTPS")
                content = f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{content}</a>'
        else:
            raise ValueError("Report items require text or a text/link object")
        output.append(f"<li>{content}</li>")
    return "".join(output)


def render(report, output_format=None):
    """Reuse the selected Skill asset while preserving every supplied person."""

    path = output_format or Path(__file__).resolve().parents[1] / "OUTPUT_FORMAT.md"
    match = re.search(r"```html\s*\n(.*?)\n```", Path(path).read_text(encoding="utf-8"), re.DOTALL)
    if match is None:
        raise ValueError("The Skill output format has no HTML skeleton")
    template = match.group(1)
    article = re.search(r"<article\b.*?</article>", template, re.DOTALL).group(0)
    css_match = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    css = re.sub(r"^.*<person-slug-\d+>.*\n?", "", css_match.group(1), flags=re.MULTILINE)
    css = re.sub(r"<!--.*?-->", "", css, flags=re.DOTALL)
    people = report.get("people")
    if not isinstance(people, list):
        raise ValueError("The report requires a list of people")
    cards, radios, labels, rules = [], [], [], []
    for index, person in enumerate(people, start=1):
        slug = f"person-{index}"
        observed = person.get("observed_completion")
        if not isinstance(observed, bool):
            raise ValueError("Each person requires an explicit observed_completion boolean")
        card = article.replace("<person-slug>", slug)
        card = card.replace("<!-- 姓名/账号 -->", text(person.get("name")))
        card = card.replace('class="card status-ok"', f'class="card {"status-ok" if observed else "status-idle"}"')
        for field in ("completed", "in_progress"):
            card = card.replace('<!-- li 列表，或"无" -->', items(person.get(field)), 1)
        card = card.replace("<!-- 风险文本 -->", text(person.get("risks")))
        card = card.replace("<!-- 评语文本 -->", text(person.get("comment")))
        cards.append(card)
        radios.append(f'<input type="radio" name="filter" id="f-{slug}">')
        labels.append(f'<label for="f-{slug}">{text(person.get("name"))}</label>')
        rules.append(f'#f-{slug}:checked ~ .board .card:not([data-name="{slug}"]) {{ display:none; }}')
    template = template[: css_match.start(1)] + css + "\n".join(rules) + "\n" + template[css_match.end(1) :]
    template = re.sub(
        r"<title>.*?</title>",
        lambda _: f'<title>{text(report.get("title"))}</title>',
        template,
        count=1,
        flags=re.DOTALL,
    )
    template = re.sub(
        r"<h1>.*?</h1>", lambda _: f'<h1>{text(report.get("title"))}</h1>', template, count=1, flags=re.DOTALL
    )
    template = re.sub(
        r"<p><!--.*?--></p>", lambda _: f'<p>{text(report.get("coverage"))}</p>', template, count=1, flags=re.DOTALL
    )
    template = re.sub(r"<!-- 每人一个 radio.*?-->", lambda _: "\n".join(radios), template, count=1)
    template = re.sub(r"<!-- 每人一个 label.*?-->", lambda _: "\n".join(labels), template, count=1)
    template = re.sub(
        r'(<main class="board">).*?(</main>)',
        lambda m: m[1] + "\n".join(cards) + m[2],
        template,
        count=1,
        flags=re.DOTALL,
    )
    return template + "\n"


def main():
    """Read authored JSON from stdin and save one final file for host delivery."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    directory = os.environ.get("SOURCELENS_DELIVERABLE_DIR")
    if directory:
        output.relative_to(Path(directory).resolve())
    content = render(json.load(sys.stdin))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(json.dumps({"ok": True, "file": output.name, "bytes": output.stat().st_size}))


if __name__ == "__main__":
    main()
