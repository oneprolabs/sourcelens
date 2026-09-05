# Plugin Package Contract

## Objective

Keep Plugin discovery simple and filesystem-owned. A Plugin manifest is part of
the installed package and is the only source of its display metadata, schemas,
capabilities, and business version. SourceLens does not persist a separate
manifest, release state, or package digest in the database.

## Package layout

Each installed Plugin uses one flat directory under a trusted root:

```text
plugins/<plugin-key>/
  plugin.json
  control.py
  runtime.py
  assets/
```

`plugin.json` must declare a valid `key`, SemVer `version`, supported protocol,
handlers, and the schemas required by its capabilities. The registry validates
the manifest, entrypoints, icon, and datasource/tool declarations before making
the Plugin available.

## Resolution

- The registry discovers packages directly from configured filesystem roots.
- When a key is requested without a version, the highest installed SemVer is
  selected.
- A requested `plugin_key + plugin_version` is resolved exactly from the
  installed package.
- Connection, datasource, assistant, and execution code use the same registry;
  no database release record is consulted.
- `ExecutionSnapshot.plugin_version` remains so a run records which business
  version it used. It is an execution audit field, not release metadata.

## Database boundary

The `PluginRelease` model and its lifecycle endpoints are removed. Migration
`0049_remove_plugin_release` drops the old table from upgraded databases. The
historical migration that created the table remains in the migration graph so
existing installations can upgrade safely.

The database continues to store Plugin bindings and execution snapshots, but it
does not store a copy of `plugin.json`, release status, deployment roles, or a
package digest. Editing a trusted installed package therefore takes effect on
the next registry read; it does not produce a
`PLUGIN_RELEASE_DIGEST_MISMATCH` error.

## Boundaries

- Plugin files are trusted deployment artifacts and must remain inside the
  configured Plugin root.
- The registry rejects path escapes, symlinks at package boundaries, invalid
  identities, unsupported handlers, unsafe schemas, and invalid capabilities.
- ZIP upload, third-party package execution, signature verification, and
  isolated Plugin processes are outside this contract.
- Production image builds must copy the flat `plugins/` tree into the runtime
  image.

## Verification

Registry tests cover flat discovery, exact version lookup, SemVer selection,
manifest validation, and acceptance of a modified installed package without a
persisted digest. API and LensNode tests cover Plugin bindings, snapshots, and
runtime resolution using the filesystem manifest.
