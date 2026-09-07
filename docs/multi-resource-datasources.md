# Multi-resource Git DataSources

## Objective

Allow one GitHub or GitLab DataSource to synchronize multiple authorized
repositories/projects. Preserve the current workspace layout and reduce the
production catalog from 48 split GitLab rows to the original 16 logical data
sources.

## Contract

- GitHub datasource config accepts `repositories` (1-50 values).
- GitLab datasource config accepts `projects` (1-50 values).
- Existing singular `repository`/`project` configs remain readable during the
  expand phase.
- Plugin runtimes always emit a `repositories` collection, including for one
  repository. LensNode uses the same synchronization path for singular and
  plural configurations.
- `target_path` is always the DataSource workspace root. A new repository is
  stored at `target_path/<canonical-resource-id>`, for example
  `target_path/HyperBDR/docs` or `target_path/group/subgroup/project`.
- Canonical resource IDs are validated as safe POSIX-relative paths. Absolute
  paths, traversal segments, empty segments, backslashes, and unsafe filename
  characters are rejected before filesystem access.
- Connection allowlists remain authoritative and are revalidated per resource.
- Repository manifests retain stable Git source IDs based on repository URL,
  branch, and repository-relative file path. DataSource-level paths include the
  canonical resource ID so repositories with the same filename remain
  distinguishable.

## Existing workspace compatibility

Deploying the canonical layout does not relocate production data. Before using
the canonical target, LensNode recognizes an existing repository only when it
contains `.git` and either a DataSource marker owned by the current
`datasource_uuid` or, for an unmarked incomplete synchronization, an exact
matching Git remote. Supported legacy layouts are:

- a singular repository stored directly at `target_path`;
- an organization repository stored at `target_path/<repository-name>`;
- an encoded multi-resource directory such as
  `target_path/group%2Frepository`.

An owned legacy repository continues to synchronize in place. A missing or new
repository uses the canonical layout. Cleanup walks nested namespace paths but
only removes repository roots whose marker belongs to the current DataSource;
foreign and unmarked directories are preserved.

If a singular DataSource already has a root manifest but no canonical or owned
legacy Git repository can be identified, synchronization stops with
`LENS_SOURCE_GIT_LAYOUT_MIGRATION_REQUIRED`. It does not clone another copy or
guess which existing directory should be reused.

Workspace code search already scans recursively. Git history and recent-change
tools also discover repositories recursively and stop at each Git root, so
canonical namespace directories remain usable for code analysis.

## Completed catalog consolidation

- Keep the 5 GitHub and 9 Feishu DataSources unchanged.
- Consolidate GitLab resources into two DataSources: `atomy` (10 projects) and
  `hypermotion` (24 projects), retaining their existing sync policies and
  workspace paths.
- Repoint historical ExecutionSnapshot and PluginInvocation rows to the
  consolidated DataSource while retaining resolved resource and target data.
- Replace old schedules with one schedule per consolidated DataSource.
- Delete the 32 split GitLab DataSource rows only after references are moved in
  one transaction and a production backup is verified.

## Pending storage migration

Production storage remains on its current compatible layout after deployment.
Moving an existing repository to the canonical path or rebuilding it with a
fresh synchronization is a separate operational decision. Either operation
must pause the affected schedule, verify repository identity and the DataSource
marker, and validate synchronization and code analysis before legacy paths are
removed.

## Verification

- Provider/unit tests cover singular compatibility, multi-resource validation,
  scope rejection, and deterministic target paths.
- Production audit reports 16 active DataSources, no protected references,
  unique target paths, and complete schedules.
