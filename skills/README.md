# Report Skill reference fixture

`engineering-report/` is a reference and test fixture copied from an uploaded Skill.
It owns report composition, source-fetch strategy, the HTML skeleton and the
script that fills it with model-authored content. Source Plugins do not register
or activate this workflow.

Uploaded Skill packages are user-owned configuration. Platform performance fixes
must work with the bound package and must not rewrite or upload a replacement.
This directory is not a platform default or an automatic update source. A user
can explicitly update their complete package through the existing Skill update
action. Files materialized inside a Run are execution copies.

The renderer uses the existing `run_skill_script` tool, receives authored JSON
through stdin and writes into `SOURCELENS_DELIVERABLE_DIR`. It reads the HTML
block from `OUTPUT_FORMAT.md`; it does not store a separate host template or
generate business conclusions. Source evidence is read through the generic
`read_tool_evidence` tool. Explicit user output preferences take precedence.
