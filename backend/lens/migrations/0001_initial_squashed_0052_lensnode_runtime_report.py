import django.db.migrations.operations.special
import django.db.models.deletion
import lens.models
import importlib
import uuid
from django.conf import settings
from django.db import migrations, models


# These historical data migrations are imported while the original migration
# modules remain available for already-deployed databases. They can be inlined
# before the old migration files are removed after every environment has
# migrated to this squashed migration.
# lens.migrations.0009_normalize_datasource_status
# lens.migrations.0011_assistant_visibility_assistantaccess
# lens.migrations.0014_datasourcecredential_endpoint_scope
# lens.migrations.0026_archive_assistants
# lens.migrations.0028_runexecution_agent_rounds_runexecution_run_timeout_s
# lens.migrations.0044_skill_metadata_and_workspace_guide
# lens.migrations.0045_orchestrator_capability
# lens.migrations.0047_plugin_integrations
# lens.migrations.0048_sharedqa_content_language

_migration_0009 = importlib.import_module(
    "lens.migrations.0009_normalize_datasource_status"
)
_migration_0011 = importlib.import_module(
    "lens.migrations.0011_assistant_visibility_assistantaccess"
)
_migration_0014 = importlib.import_module(
    "lens.migrations.0014_datasourcecredential_endpoint_scope"
)
_migration_0026 = importlib.import_module(
    "lens.migrations.0026_archive_assistants"
)
_migration_0028 = importlib.import_module(
    "lens.migrations.0028_runexecution_agent_rounds_runexecution_run_timeout_s"
)
_migration_0044 = importlib.import_module(
    "lens.migrations.0044_skill_metadata_and_workspace_guide"
)
_migration_0045 = importlib.import_module(
    "lens.migrations.0045_orchestrator_capability"
)
_migration_0047 = importlib.import_module(
    "lens.migrations.0047_plugin_integrations"
)
_migration_0048 = importlib.import_module(
    "lens.migrations.0048_sharedqa_content_language"
)
_migration_0050 = importlib.import_module(
    "lens.migrations.0050_taskexecution_datasource_history_index"
)

class Migration(migrations.Migration):

    replaces = [('lens', '0001_initial'), ('lens', '0002_assistant_postprocess_model_ref_mcpserver_version_and_more'), ('lens', '0003_assistant_agent_rounds'), ('lens', '0004_assistant_max_concurrency'), ('lens', '0005_datasource_lensnode'), ('lens', '0006_datasourcecredential_datasource_credential'), ('lens', '0007_datasourcecredential_feishu_choices'), ('lens', '0008_datasource_last_error'), ('lens', '0009_normalize_datasource_status'), ('lens', '0010_sharedqa'), ('lens', '0011_assistant_visibility_assistantaccess'), ('lens', '0012_alter_runstep_step_type_messageattachment'), ('lens', '0013_run_last_activity_at'), ('lens', '0014_datasourcecredential_endpoint_scope'), ('lens', '0015_datasourcecredential_no_auth'), ('lens', '0016_skill_package_fields'), ('lens', '0017_add_general_chat_run_step_type'), ('lens', '0018_runoutputfile'), ('lens', '0019_lensnode_disconnected_at'), ('lens', '0020_assistant_description'), ('lens', '0021_environmentvariableset_assistantskill_environment'), ('lens', '0021_alter_datasourcecredential_auth_type'), ('lens', '0022_merge_20260725_1058'), ('lens', '0023_sharedqafile'), ('lens', '0024_assistant_runexecution_token_budget'), ('lens', '0025_run_outcome_termination_detail'), ('lens', '0026_archive_assistants'), ('lens', '0027_run_feedback'), ('lens', '0028_runexecution_agent_rounds_runexecution_run_timeout_s'), ('lens', '0029_datasource_managed_workspace'), ('lens', '0030_alter_token_budget_profile_choices'), ('lens', '0031_run_retry_of_run'), ('lens', '0032_session_semantic_title_state'), ('lens', '0033_session_pinned_at'), ('lens', '0034_runexecution_runtime_snapshot_rundiagnosticevidence_and_more'), ('lens', '0035_rundiagnostic_language'), ('lens', '0036_rundiagnostic_progress'), ('lens', '0037_datasource_conversion_status'), ('lens', '0038_run_awaiting_resume'), ('lens', '0039_runexecution_admission_state'), ('lens', '0040_mcp_environment_bindings'), ('lens', '0041_run_citations_and_planned_evidence'), ('lens', '0042_run_clarification_state'), ('lens', '0043_runtraceevent'), ('lens', '0044_skill_metadata_and_workspace_guide'), ('lens', '0045_orchestrator_capability'), ('lens', '0046_assistant_fixed_collaboration'), ('lens', '0047_plugin_integrations'), ('lens', '0048_sharedqa_content_language'), ('lens', '0049_remove_plugin_release'), ('lens', '0050_taskexecution_datasource_history_index'), ('lens', '0051_datasource_items_session_snapshots'), ('lens', '0052_lensnode_runtime_report')]

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='GlobalSetting',
            fields=[
                ('key', models.CharField(max_length=190, primary_key=True, serialize=False)),
                ('value', models.JSONField(blank=True, default=dict)),
                ('description', models.CharField(blank=True, default='', max_length=255)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='MCPServer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('transport', models.CharField(choices=[('url', 'URL'), ('stdio', 'STDIO')], max_length=16)),
                ('endpoint', models.CharField(blank=True, default='', max_length=500)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('enabled', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Message',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('role', models.CharField(choices=[('user', 'User'), ('assistant', 'Assistant'), ('system', 'System')], max_length=16)),
                ('content', models.TextField(blank=True, default='')),
                ('sequence', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['sequence'],
            },
        ),
        migrations.CreateModel(
            name='LensNode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('status', models.CharField(choices=[('online', 'Online'), ('offline', 'Offline'), ('draining', 'Draining')], default='offline', max_length=16)),
                ('connection_id', models.CharField(blank=True, default='', max_length=128)),
                ('workspace_path', models.CharField(blank=True, default='', max_length=500)),
                ('available_dirs', models.JSONField(blank=True, default=list)),
                ('protocol_version', models.CharField(blank=True, default='', max_length=32)),
                ('agent_version', models.CharField(blank=True, default='', max_length=64)),
                ('tasks', models.JSONField(blank=True, default=list)),
                ('labels', models.JSONField(blank=True, default=dict)),
                ('enrollment_status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=16)),
                ('auth_token_hash', models.CharField(blank=True, default='', max_length=128)),
                ('token_issued_at', models.DateTimeField(blank=True, null=True)),
                ('token_revoked', models.BooleanField(default=False)),
                ('last_authenticated_at', models.DateTimeField(blank=True, null=True)),
                ('last_heartbeat_at', models.DateTimeField(blank=True, null=True)),
                ('registered_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['name'],
                'indexes': [models.Index(fields=['status'], name='lens_lensnode_status_idx'), models.Index(fields=['enrollment_status'], name='lens_lensnode_enroll_idx')],
            },
        ),
        migrations.CreateModel(
            name='Skill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('slug', models.SlugField(max_length=180, unique=True)),
                ('definition', models.JSONField(blank=True, default=dict)),
                ('enabled', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='DataSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('source_type', models.CharField(choices=[('git', 'Git'), ('feishu', 'Feishu'), ('jira', 'Jira')], max_length=32)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('sync_policy', models.JSONField(blank=True, default=dict)),
                ('target_path', models.CharField(blank=True, default='', max_length=500)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled'), ('error', 'Error')], default='active', max_length=16)),
            ],
            options={
                'ordering': ['name'],
                'indexes': [models.Index(fields=['status'], name='lens_datasource_status_idx')],
            },
        ),
        migrations.CreateModel(
            name='Assistant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('slug', models.SlugField(max_length=180, unique=True)),
                ('selected_task', models.CharField(max_length=160)),
                ('selected_dirs', models.JSONField(blank=True, default=list)),
                ('preprocess_model_ref', models.UUIDField(blank=True, null=True)),
                ('multimodal_model_ref', models.UUIDField(blank=True, null=True)),
                ('agent_model_ref', models.UUIDField(blank=True, null=True)),
                ('settings', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled')], default='active', max_length=16)),
                ('lensnode', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assistants', to='lens.lensnode')),
            ],
            options={
                'ordering': ['name'],
                'indexes': [models.Index(fields=['lensnode'], name='lens_assistant_lensnode_idx')],
            },
        ),
        migrations.CreateModel(
            name='Session',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(blank=True, default='', max_length=160)),
                ('status', models.CharField(choices=[('active', 'Active'), ('archived', 'Archived')], default='active', max_length=16)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.assistant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Run',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('streaming', 'Streaming'), ('done', 'Done'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True, default='queued', max_length=16)),
                ('metering_ref', models.UUIDField(blank=True, null=True)),
                ('error', models.TextField(blank=True, default='')),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('idempotency_key', models.CharField(blank=True, default='', max_length=128)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('input_message', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='request_runs', to='lens.message')),
                ('lensnode', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='runs', to='lens.lensnode')),
                ('output_message', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='response_runs', to='lens.message')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='lens.session')),
            ],
            options={
                'ordering': ['-started_at', '-created_at'],
            },
        ),
        migrations.AddField(
            model_name='message',
            name='run',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='messages', to='lens.run'),
        ),
        migrations.AddField(
            model_name='message',
            name='session',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='lens.session'),
        ),
        migrations.CreateModel(
            name='AssistantSkill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=True)),
                ('load_config', models.JSONField(blank=True, default=dict)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='skill_bindings', to='lens.assistant')),
                ('skill', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.skill')),
            ],
            options={
                'unique_together': {('assistant', 'skill')},
            },
        ),
        migrations.CreateModel(
            name='AssistantMCP',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=True)),
                ('load_config', models.JSONField(blank=True, default=dict)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mcp_bindings', to='lens.assistant')),
                ('mcp', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.mcpserver')),
            ],
            options={
                'unique_together': {('assistant', 'mcp')},
            },
        ),
        migrations.CreateModel(
            name='RunExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('task', models.CharField(max_length=160)),
                ('loaded_skills', models.JSONField(blank=True, default=list)),
                ('loaded_mcps', models.JSONField(blank=True, default=list)),
                ('target_dirs', models.JSONField(blank=True, default=list)),
                ('status', models.CharField(choices=[('dispatched', 'Dispatched'), ('running', 'Running'), ('completed', 'Completed'), ('failed', 'Failed')], default='dispatched', max_length=16)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('lensnode', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.lensnode')),
                ('run', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='execution', to='lens.run')),
            ],
            options={
                'indexes': [models.Index(fields=['lensnode'], name='lens_runexec_lensnode_idx')],
            },
        ),
        migrations.CreateModel(
            name='RunStep',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('step_type', models.CharField(choices=[('query_rewrite', 'Query Rewrite'), ('retrieval', 'Retrieval'), ('answer', 'Answer'), ('stream', 'Stream')], max_length=32)),
                ('detail', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('running', 'Running'), ('done', 'Done'), ('failed', 'Failed')], max_length=16)),
                ('sequence', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='lens.run')),
            ],
            options={
                'ordering': ['sequence'],
                'indexes': [models.Index(fields=['run', 'sequence'], name='lens_runstep_run_seq_idx')],
                'unique_together': {('run', 'sequence')},
            },
        ),
        migrations.CreateModel(
            name='ScheduledTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('task_type', models.CharField(choices=[('source_sync', 'Source Sync'), ('lensnode_cleanup', 'LensNode Cleanup'), ('run_retention', 'Run Retention'), ('lensnode_health', 'LensNode Health')], max_length=32)),
                ('periodic_task_ref', models.IntegerField(blank=True, null=True)),
                ('target_type', models.CharField(blank=True, max_length=64, null=True)),
                ('target_id', models.UUIDField(blank=True, null=True)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('last_status', models.CharField(blank=True, choices=[('success', 'Success'), ('failed', 'Failed'), ('running', 'Running')], max_length=16, null=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('last_metrics', models.JSONField(blank=True, default=dict)),
                ('enabled', models.BooleanField(default=True)),
            ],
            options={
                'indexes': [models.Index(fields=['task_type'], name='lens_sched_task_type_idx'), models.Index(fields=['target_type', 'target_id'], name='lens_sched_target_idx')],
            },
        ),
        migrations.AddIndex(
            model_name='run',
            index=models.Index(fields=['session', 'status'], name='lens_run_session_status_idx'),
        ),
        migrations.AddIndex(
            model_name='run',
            index=models.Index(fields=['lensnode'], name='lens_run_lensnode_idx'),
        ),
        migrations.AddConstraint(
            model_name='run',
            constraint=models.UniqueConstraint(condition=models.Q(('idempotency_key', ''), _negated=True), fields=('session', 'idempotency_key'), name='lens_run_idem_nonempty_uniq'),
        ),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['session', 'sequence'], name='lens_message_session_seq_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='message',
            unique_together={('session', 'sequence')},
        ),
        migrations.AddField(
            model_name='assistant',
            name='postprocess_model_ref',
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mcpserver',
            name='version',
            field=models.CharField(blank=True, default='1', max_length=64),
        ),
        migrations.AddField(
            model_name='skill',
            name='version',
            field=models.CharField(blank=True, default='1', max_length=64),
        ),
        migrations.AddField(
            model_name='assistant',
            name='agent_rounds',
            field=models.CharField(choices=[('flash', '极速'), ('fast', '快速'), ('balanced', '均衡'), ('deep', '深度'), ('max', '极限')], default='balanced', max_length=16),
        ),
        migrations.AddField(
            model_name='assistant',
            name='max_concurrency',
            field=models.PositiveSmallIntegerField(default=5),
        ),
        migrations.AddField(
            model_name='datasource',
            name='lensnode',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='datasources', to='lens.lensnode'),
        ),
        migrations.AlterField(
            model_name='datasource',
            name='source_type',
            field=models.CharField(choices=[('git', 'Git'), ('feishu', 'Feishu')], max_length=32),
        ),
        migrations.AddIndex(
            model_name='datasource',
            index=models.Index(fields=['lensnode'], name='lens_datasource_lensnode_idx'),
        ),
        migrations.CreateModel(
            name='DataSourceCredential',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('provider', models.CharField(choices=[('github', 'GitHub'), ('gitlab', 'GitLab'), ('feishu', 'Feishu'), ('generic', 'Generic')], default='generic', max_length=32)),
                ('auth_type', models.CharField(choices=[('https_token', 'HTTPS Token'), ('feishu_app', 'Feishu App')], default='https_token', max_length=32)),
                ('encrypted_secret', models.TextField(blank=True, default='')),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='datasource',
            name='credential',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='datasources', to='lens.datasourcecredential'),
        ),
        migrations.AddField(
            model_name='datasource',
            name='last_error',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.RunPython(
            code=_migration_0009.normalize_datasource_status,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='datasource',
            name='status',
            field=models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled')], default='active', max_length=16),
        ),
        migrations.CreateModel(
            name='SharedQA',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('token', models.CharField(max_length=64, unique=True)),
                ('assistant_name', models.CharField(blank=True, default='', max_length=160)),
                ('assistant_slug', models.SlugField(blank=True, default='', max_length=180)),
                ('question', models.TextField(blank=True, default='')),
                ('answer', models.TextField(blank=True, default='')),
                ('title', models.CharField(blank=True, default='', max_length=200)),
                ('is_listed', models.BooleanField(default=False)),
                ('status', models.CharField(choices=[('published', 'Published'), ('hidden', 'Hidden')], default='published', max_length=16)),
                ('view_count', models.PositiveIntegerField(default=0)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('assistant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shared_qas', to='lens.assistant')),
                ('published_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shared_qas', to=settings.AUTH_USER_MODEL)),
                ('run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shares', to='lens.run')),
            ],
            options={
                'ordering': ['-published_at', '-created_at'],
                'indexes': [models.Index(fields=['assistant', 'is_listed', 'status', '-published_at'], name='lens_sharedqa_list_idx')],
            },
        ),
        migrations.AddField(
            model_name='assistant',
            name='visibility',
            field=models.CharField(choices=[('public', 'Public'), ('private', 'Private')], default='private', max_length=16),
        ),
        migrations.RunPython(
            code=_migration_0011.set_existing_assistants_public,
            reverse_code=_migration_0011.noop,
        ),
        migrations.CreateModel(
            name='AssistantAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('level', models.CharField(default='view', max_length=16)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='access_grants', to='lens.assistant')),
                ('granted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='assistant_grants', to='auth.group')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='assistant_grants', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['assistant'], name='lens_aaccess_assistant_idx')],
                'constraints': [models.CheckConstraint(condition=models.Q(models.Q(('group__isnull', False), ('user__isnull', True)), models.Q(('group__isnull', True), ('user__isnull', False)), _connector='OR'), name='assistant_access_group_xor_user'), models.UniqueConstraint(condition=models.Q(('group__isnull', False)), fields=('assistant', 'group'), name='uniq_assistant_group'), models.UniqueConstraint(condition=models.Q(('user__isnull', False)), fields=('assistant', 'user'), name='uniq_assistant_user')],
            },
        ),
        migrations.AlterField(
            model_name='runstep',
            name='step_type',
            field=models.CharField(choices=[('query_rewrite', 'Query Rewrite'), ('multimodal', 'Multimodal'), ('retrieval', 'Retrieval'), ('answer', 'Answer'), ('stream', 'Stream')], max_length=32),
        ),
        migrations.CreateModel(
            name='MessageAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('image', 'Image')], default='image', max_length=16)),
                ('file', models.ImageField(upload_to=lens.models.message_attachment_upload_to)),
                ('original_name', models.CharField(blank=True, default='', max_length=255)),
                ('mime_type', models.CharField(blank=True, default='', max_length=80)),
                ('byte_size', models.PositiveIntegerField(default=0)),
                ('width', models.PositiveIntegerField(blank=True, null=True)),
                ('height', models.PositiveIntegerField(blank=True, null=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('message', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='lens.message')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='lens.session')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lens_attachments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['order', 'created_at'],
                'indexes': [models.Index(fields=['message', 'order'], name='lens_attach_msg_order_idx'), models.Index(fields=['session'], name='lens_attach_session_idx')],
            },
        ),
        migrations.AddField(
            model_name='run',
            name='last_activity_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='datasourcecredential',
            name='endpoint_url',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='datasourcecredential',
            name='scope_config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='datasourcecredential',
            name='sync_scope',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='datasourcecredential',
            name='validated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='datasourcecredential',
            name='validation_message',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='datasourcecredential',
            name='validation_status',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.RunPython(
            code=_migration_0014.populate_credential_defaults,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.AddField(
            model_name='skill',
            name='package_hash',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='skill',
            name='package_manifest',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='skill',
            name='package_path',
            field=models.CharField(blank=True, default='', max_length=700),
        ),
        migrations.AddField(
            model_name='skill',
            name='package_size',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='skill',
            name='source_type',
            field=models.CharField(blank=True, default='manual', max_length=32),
        ),
        migrations.AddField(
            model_name='skill',
            name='source_url',
            field=models.CharField(blank=True, default='', max_length=1000),
        ),
        migrations.AlterField(
            model_name='runstep',
            name='step_type',
            field=models.CharField(choices=[('query_rewrite', 'Query Rewrite'), ('multimodal', 'Multimodal'), ('retrieval', 'Retrieval'), ('general_chat', 'General Chat'), ('answer', 'Answer'), ('stream', 'Stream')], max_length=32),
        ),
        migrations.CreateModel(
            name='RunOutputFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(storage=lens.models.deliverable_storage, upload_to=lens.models.run_output_file_upload_to)),
                ('filename', models.CharField(max_length=255)),
                ('content_type', models.CharField(blank=True, default='', max_length=120)),
                ('byte_size', models.PositiveIntegerField(default=0)),
                ('content_hash', models.CharField(blank=True, default='', max_length=64)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='output_files', to='lens.assistant')),
                ('message', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='output_files', to='lens.message')),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='output_files', to='lens.run')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='output_files', to='lens.session')),
            ],
            options={
                'ordering': ['created_at'],
                'indexes': [models.Index(fields=['message'], name='lens_outfile_msg_idx'), models.Index(fields=['session'], name='lens_outfile_sess_idx')],
            },
        ),
        migrations.AddField(
            model_name='lensnode',
            name='disconnected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assistant',
            name='description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.CreateModel(
            name='EnvironmentVariableSet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160, unique=True)),
                ('description', models.TextField(blank=True, default='')),
                ('encrypted_values', models.TextField(blank=True, default='')),
                ('enabled', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='assistantskill',
            name='environment_variable_set',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='skill_bindings', to='lens.environmentvariableset'),
        ),
        migrations.AlterField(
            model_name='datasourcecredential',
            name='auth_type',
            field=models.CharField(choices=[('none', 'Public Access'), ('https_token', 'HTTPS Token'), ('feishu_app', 'Feishu App')], default='https_token', max_length=32),
        ),
        migrations.CreateModel(
            name='SharedQAFile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('input', 'Input attachment'), ('output', 'Output deliverable')], max_length=16)),
                ('source_uuid', models.UUIDField(blank=True, null=True)),
                ('file', models.FileField(storage=lens.models.deliverable_storage, upload_to=lens.models.shared_qa_file_upload_to)),
                ('filename', models.CharField(max_length=255)),
                ('content_type', models.CharField(blank=True, default='', max_length=120)),
                ('byte_size', models.PositiveIntegerField(default=0)),
                ('order', models.PositiveIntegerField(default=0)),
                ('share', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='files', to='lens.sharedqa')),
            ],
            options={
                'ordering': ['kind', 'order', 'created_at'],
                'indexes': [models.Index(fields=['share', 'kind', 'order'], name='lens_sharefile_order_idx')],
                'constraints': [models.UniqueConstraint(fields=('share', 'kind', 'source_uuid'), name='lens_sharefile_source_uniq')],
            },
        ),
        migrations.AddField(
            model_name='assistant',
            name='token_budget_profile',
            field=models.CharField(choices=[('standard', 'Standard'), ('deep', 'Deep')], default='standard', max_length=16),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='token_budget_final_reserve_tokens',
            field=models.PositiveIntegerField(default=40000),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='token_budget_max_tokens',
            field=models.PositiveIntegerField(default=200000),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='token_budget_profile',
            field=models.CharField(choices=[('standard', 'Standard'), ('deep', 'Deep')], default='standard', max_length=16),
        ),
        migrations.AddField(
            model_name='run',
            name='outcome',
            field=models.CharField(blank=True, choices=[('completed', 'Completed'), ('partial', 'Partial'), ('blocked', 'Blocked')], default='', max_length=16),
        ),
        migrations.AddField(
            model_name='run',
            name='termination_detail',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(
            code=_migration_0026.archive_disabled_assistants,
            reverse_code=_migration_0026.disable_archived_assistants,
        ),
        migrations.AlterField(
            model_name='assistant',
            name='status',
            field=models.CharField(choices=[('active', 'Active'), ('archived', 'Archived')], default='active', max_length=16),
        ),
        migrations.AddField(
            model_name='run',
            name='feedback',
            field=models.CharField(blank=True, choices=[('positive', 'Positive'), ('negative', 'Negative')], default='', max_length=16),
        ),
        migrations.AddField(
            model_name='run',
            name='feedback_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='agent_rounds',
            field=models.CharField(choices=[('flash', '极速'), ('fast', '快速'), ('balanced', '均衡'), ('deep', '深度'), ('max', '极限')], default=None, max_length=16, null=True),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='run_timeout_s',
            field=models.PositiveIntegerField(default=None, null=True),
        ),
        migrations.AlterField(
            model_name='runexecution',
            name='status',
            field=models.CharField(choices=[('queued', 'Queued'), ('dispatched', 'Dispatched'), ('running', 'Running'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], default='queued', max_length=16),
        ),
        migrations.RunPython(
            code=_migration_0028.backfill_run_timeout_snapshots,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='datasource',
            name='source_type',
            field=models.CharField(choices=[('git', 'Git'), ('feishu', 'Feishu'), ('managed_workspace', 'Managed Workspace')], max_length=32),
        ),
        migrations.AddField(
            model_name='datasource',
            name='availability_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='datasource',
            name='availability_message',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='datasource',
            name='availability_status',
            field=models.CharField(choices=[('unknown', 'Unknown'), ('available', 'Available'), ('unavailable', 'Unavailable'), ('error', 'Error')], default='unknown', max_length=16),
        ),
        migrations.AlterField(
            model_name='assistant',
            name='token_budget_profile',
            field=models.CharField(choices=[('standard', 'Standard'), ('deep', 'Deep'), ('unlimited', 'Unlimited')], default='standard', max_length=16),
        ),
        migrations.AlterField(
            model_name='runexecution',
            name='token_budget_profile',
            field=models.CharField(choices=[('standard', 'Standard'), ('deep', 'Deep'), ('unlimited', 'Unlimited')], default='standard', max_length=16),
        ),
        migrations.AddField(
            model_name='run',
            name='retry_of_run',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='retry_runs', to='lens.run'),
        ),
        migrations.AddField(
            model_name='session',
            name='title_generation_status',
            field=models.CharField(choices=[('skipped', 'Skipped'), ('pending', 'Pending'), ('generating', 'Generating'), ('generated', 'Generated'), ('failed', 'Failed')], db_default='skipped', default='skipped', max_length=16),
        ),
        migrations.AddField(
            model_name='session',
            name='title_manually_edited',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name='session',
            name='pinned_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='runtime_snapshot',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='RunDiagnosticEvidence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('schema_version', models.PositiveSmallIntegerField(default=1)),
                ('payload', models.JSONField(default=dict)),
                ('payload_hash', models.CharField(max_length=64)),
                ('run', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='diagnostic_evidence', to='lens.run')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='RunDiagnostic',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('completed', 'Completed'), ('failed', 'Failed')], default='queued', max_length=16)),
                ('deterministic_findings', models.JSONField(blank=True, default=list)),
                ('model_ref', models.UUIDField(blank=True, null=True)),
                ('model_config_hash', models.CharField(blank=True, default='', max_length=64)),
                ('prompt_version', models.CharField(default='run-diagnosis-v1', max_length=32)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('usage', models.JSONField(blank=True, default=dict)),
                ('error_code', models.CharField(blank=True, default='', max_length=64)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('idempotency_key', models.CharField(default='initial-v1', max_length=128)),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requested_run_diagnostics', to=settings.AUTH_USER_MODEL)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='diagnostics', to='lens.run')),
                ('superseded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='superseded_diagnostics', to='lens.rundiagnostic')),
                ('evidence', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='diagnostics', to='lens.rundiagnosticevidence')),
            ],
            options={
                'permissions': [('run_diagnostics', 'Can run evidence-backed diagnostics')],
            },
        ),
        migrations.CreateModel(
            name='RunDiagnosticTurn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('completed', 'Completed'), ('failed', 'Failed')], default='queued', max_length=16)),
                ('question', models.TextField(max_length=2000)),
                ('answer', models.TextField(blank=True, default='')),
                ('evidence_refs', models.JSONField(blank=True, default=list)),
                ('usage', models.JSONField(blank=True, default=dict)),
                ('error_code', models.CharField(blank=True, default='', max_length=64)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('idempotency_key', models.CharField(max_length=64)),
                ('diagnostic', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='turns', to='lens.rundiagnostic')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requested_run_diagnostic_turns', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='RunTraceExport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('running', 'Running'), ('completed', 'Completed'), ('retrying', 'Retrying'), ('failed', 'Failed'), ('skipped', 'Skipped')], default='pending', max_length=16)),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('last_error_category', models.CharField(blank=True, default='', max_length=64)),
                ('exported_at', models.DateTimeField(blank=True, null=True)),
                ('run', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='trace_export', to='lens.run')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='rundiagnostic',
            index=models.Index(fields=['run', 'status'], name='lens_diag_run_status_idx'),
        ),
        migrations.AddConstraint(
            model_name='rundiagnostic',
            constraint=models.UniqueConstraint(fields=('run', 'idempotency_key'), name='lens_diag_run_idem_uniq'),
        ),
        migrations.AddConstraint(
            model_name='rundiagnosticturn',
            constraint=models.UniqueConstraint(fields=('diagnostic', 'idempotency_key'), name='lens_diag_turn_idem_uniq'),
        ),
        migrations.AddField(
            model_name='rundiagnostic',
            name='language',
            field=models.CharField(default='en', help_text='UI language active when the diagnosis was requested.', max_length=16),
        ),
        migrations.AddField(
            model_name='rundiagnostic',
            name='progress',
            field=models.JSONField(blank=True, default=dict, help_text='Execution stage and detail while the diagnosis is pending.'),
        ),
        migrations.AddField(
            model_name='datasource',
            name='last_conversion_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='datasource',
            name='last_conversion_status',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AddField(
            model_name='run',
            name='resume_by',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='dispatch_id',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='admitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='runexecution',
            name='checkpoint_ready_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='mcpserver',
            name='environment',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='assistantmcp',
            name='environment_variable_set',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='mcp_bindings', to='lens.environmentvariableset'),
        ),
        migrations.AddField(
            model_name='run',
            name='citations',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='run',
            name='planned_evidence',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='run',
            name='clarification_answered_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='run',
            name='status',
            field=models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('streaming', 'Streaming'), ('awaiting_user_input', 'Awaiting user input'), ('done', 'Done'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], db_index=True, default='queued', max_length=24),
        ),
        migrations.CreateModel(
            name='RunTraceEvent',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_id', models.UUIDField()),
                ('sequence', models.PositiveBigIntegerField()),
                ('attempt', models.PositiveIntegerField(default=1)),
                ('event_type', models.CharField(max_length=128)),
                ('timestamp', models.DateTimeField()),
                ('checkpoint_id', models.CharField(blank=True, default='', max_length=128)),
                ('turn', models.PositiveIntegerField(blank=True, null=True)),
                ('step', models.PositiveIntegerField(blank=True, null=True)),
                ('call_id', models.CharField(blank=True, default='', max_length=128)),
                ('parent_call_id', models.CharField(blank=True, default='', max_length=128)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='trace_events', to='lens.run')),
            ],
            options={
                'ordering': ['sequence'],
                'indexes': [models.Index(fields=['run', 'sequence'], name='lens_trace_run_seq_idx'), models.Index(fields=['run', 'call_id'], name='lens_trace_run_call_idx'), models.Index(fields=['run', 'event_type'], name='lens_trace_run_type_idx')],
                'constraints': [models.UniqueConstraint(fields=('run', 'event_id'), name='lens_trace_run_event_uniq'), models.UniqueConstraint(fields=('run', 'sequence'), name='lens_trace_run_seq_uniq')],
            },
        ),
        migrations.AddField(
            model_name='skill',
            name='source_ref',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='skill',
            name='source_path',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='skill',
            name='latest_source_ref',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='skill',
            name='source_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='skill',
            name='package_name',
            field=models.CharField(blank=True, default='', max_length=180),
        ),
        migrations.AddField(
            model_name='skill',
            name='kind',
            field=models.CharField(default='standard', max_length=32),
        ),
        migrations.RunPython(
            code=_migration_0044.migrate_skill_identity,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name='skill',
            name='slug',
        ),
        migrations.AddField(
            model_name='assistant',
            name='workspace_guide',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.RunPython(
            code=_migration_0044.migrate_workspace_guides,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='assistant',
            name='lensnode',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assistants', to='lens.lensnode'),
        ),
        migrations.AddField(
            model_name='assistant',
            name='capability',
            field=models.CharField(choices=[('general_chat', 'General Chat'), ('code_analysis', 'Code Analysis'), ('knowledge_qa', 'Knowledge Q&A')], default='general_chat', max_length=24),
        ),
        migrations.AddField(
            model_name='assistant',
            name='routing_description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='run',
            name='parent_run',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='delegated_runs', to='lens.run'),
        ),
        migrations.AddIndex(
            model_name='run',
            index=models.Index(fields=['parent_run'], name='lens_run_parent_idx'),
        ),
        migrations.RunPython(
            code=_migration_0045.migrate_assistant_capabilities,
        ),
        migrations.RemoveField(
            model_name='assistant',
            name='selected_task',
        ),
        migrations.AddField(
            model_name='assistant',
            name='is_system',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='session',
            name='routing_mode',
            field=models.CharField(choices=[('direct', 'Direct'), ('smart', 'Smart Collaboration')], default='direct', max_length=16),
        ),
        migrations.AddField(
            model_name='session',
            name='allowed_assistant_uuids',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='assistant',
            name='routing_mode',
            field=models.CharField(choices=[('direct', 'Direct'), ('smart', 'Smart Collaboration')], db_index=True, default='direct', max_length=16),
        ),
        migrations.AddField(
            model_name='assistant',
            name='collaboration_members',
            field=models.ManyToManyField(blank=True, related_name='collaboration_coordinators', to='lens.assistant'),
        ),
        migrations.CreateModel(
            name='Connection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('plugin_key', models.CharField(max_length=64)),
                ('endpoint', models.URLField(max_length=500)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('allowed_scope', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disabled', 'Disabled')], default='active', max_length=16)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='SecretMaterial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('status', models.CharField(default='active', max_length=16)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='PluginRelease',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('plugin_key', models.CharField(max_length=64)),
                ('version', models.CharField(max_length=32)),
                ('package_digest', models.CharField(max_length=64)),
                ('release_status', models.CharField(choices=[('debugging', 'Debugging'), ('published', 'Published'), ('retired', 'Retired')], default='debugging', max_length=16)),
                ('deployment_role', models.CharField(blank=True, choices=[('', 'None'), ('candidate', 'Candidate'), ('active', 'Active')], default='', max_length=16)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('published_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='published_plugin_releases', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['plugin_key', '-version'],
                'constraints': [models.UniqueConstraint(fields=('plugin_key', 'version'), name='lens_plugin_release_identity_uniq'), models.UniqueConstraint(condition=models.Q(('deployment_role', 'active')), fields=('plugin_key',), name='lens_plugin_release_active_uniq'), models.UniqueConstraint(condition=models.Q(('deployment_role', 'candidate')), fields=('plugin_key',), name='lens_plugin_release_candidate_uniq'), models.CheckConstraint(condition=models.Q(('deployment_role', ''), models.Q(('deployment_role__in', ['active', 'candidate']), ('release_status', 'published')), _connector='OR'), name='lens_plugin_release_role_status_ck')],
            },
        ),
        migrations.AddField(
            model_name='datasource',
            name='datasource_config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='datasource',
            name='plugin_key',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='datasource',
            name='connection',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='datasources', to='lens.connection'),
        ),
        migrations.CreateModel(
            name='SecretVersion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('encrypted_value', models.TextField()),
                ('status', models.CharField(default='active', max_length=16)),
                ('material', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='lens.secretmaterial')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ExecutionSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('datasource_sync', 'Datasource Sync'), ('tool_invoke', 'Tool Invoke')], max_length=32)),
                ('plugin_key', models.CharField(max_length=64)),
                ('plugin_version', models.CharField(max_length=32)),
                ('protocol_version', models.PositiveIntegerField()),
                ('resolved_config', models.JSONField(default=dict)),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='execution_snapshots', to='lens.connection')),
                ('datasource', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='execution_snapshots', to='lens.datasource')),
                ('secret_version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='execution_snapshots', to='lens.secretversion')),
                ('invocation_id', models.CharField(blank=True, default='', max_length=128)),
                ('run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plugin_execution_snapshots', to='lens.run')),
                ('tool_key', models.CharField(blank=True, default='', max_length=128)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddField(
            model_name='connection',
            name='secret_version',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='connections', to='lens.secretversion'),
        ),
        migrations.CreateModel(
            name='CredentialLease',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('expires_at', models.DateTimeField()),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('lensnode', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='credential_leases', to='lens.lensnode')),
                ('snapshot', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='credential_leases', to='lens.executionsnapshot')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddField(
            model_name='runexecution',
            name='loaded_plugins',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name='AssistantPluginBinding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tools', models.JSONField(blank=True, default=list)),
                ('enabled', models.BooleanField(default=True)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='plugin_bindings', to='lens.assistant')),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assistant_bindings', to='lens.connection')),
            ],
            options={
                'unique_together': {('assistant', 'connection')},
            },
        ),
        migrations.AddConstraint(
            model_name='executionsnapshot',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('datasource__isnull', False), ('invocation_id', ''), ('kind', 'datasource_sync'), ('run__isnull', True), ('tool_key', '')), models.Q(('datasource__isnull', True), ('kind', 'tool_invoke'), ('run__isnull', False), models.Q(('tool_key', ''), _negated=True), models.Q(('invocation_id', ''), _negated=True)), _connector='OR'), name='lens_snapshot_owner_kind_ck'),
        ),
        migrations.AddConstraint(
            model_name='executionsnapshot',
            constraint=models.UniqueConstraint(condition=models.Q(('kind', 'tool_invoke')), fields=('run', 'invocation_id'), name='lens_snap_run_invocation_uniq'),
        ),
        migrations.CreateModel(
            name='PluginInvocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('datasource_sync', 'Datasource Sync'), ('tool_invoke', 'Tool Invoke')], max_length=32)),
                ('plugin_key', models.CharField(max_length=64)),
                ('tool_key', models.CharField(blank=True, default='', max_length=128)),
                ('capability', models.CharField(blank=True, default='', max_length=128)),
                ('resource_summary', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('authorized', 'Authorized'), ('materialized', 'Materialized')], default='authorized', max_length=24)),
                ('materialized_at', models.DateTimeField(blank=True, null=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='plugin_invocations', to=settings.AUTH_USER_MODEL)),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plugin_invocations', to='lens.connection')),
                ('datasource', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plugin_invocations', to='lens.datasource')),
                ('lensnode', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='plugin_invocations', to='lens.lensnode')),
                ('run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='plugin_invocations', to='lens.run')),
                ('snapshot', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='invocation_audit', to='lens.executionsnapshot')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddField(
            model_name='mcpserver',
            name='connection',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='mcp_adapters', to='lens.connection'),
        ),
        migrations.AddField(
            model_name='mcpserver',
            name='tools',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name='mcpserver',
            name='transport',
            field=models.CharField(choices=[('url', 'URL'), ('stdio', 'STDIO'), ('plugin', 'Plugin Adapter')], max_length=16),
        ),
        migrations.CreateModel(
            name='LegacyIntegrationMigration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source_kind', models.CharField(choices=[('credential', 'Credential'), ('datasource', 'Datasource')], max_length=16)),
                ('source_uuid', models.UUIDField()),
                ('status', models.CharField(choices=[('migrated', 'Migrated'), ('manual_review', 'Manual Review'), ('rolled_back', 'Rolled Back')], max_length=24)),
                ('reason', models.CharField(blank=True, default='', max_length=64)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('connection', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='legacy_migration_records', to='lens.connection')),
                ('datasource', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='legacy_migration_records', to='lens.datasource')),
            ],
            options={
                'ordering': ['source_kind', 'source_uuid'],
                'constraints': [models.UniqueConstraint(fields=('source_kind', 'source_uuid'), name='lens_legacy_migration_source_uniq')],
            },
        ),
        migrations.AlterField(
            model_name='datasource',
            name='source_type',
            field=models.CharField(choices=[('git', 'Git'), ('feishu', 'Feishu'), ('jira', 'Jira'), ('managed_workspace', 'Managed Workspace')], max_length=32),
        ),
        migrations.AlterField(
            model_name='assistant',
            name='routing_mode',
            field=models.CharField(choices=[('direct', 'Standard Mode'), ('smart', 'Smart Collaboration')], db_index=True, default='direct', max_length=16),
        ),
        migrations.RunPython(
            code=_migration_0047.bind_token_budget_to_agent_rounds,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.RunPython(
            code=_migration_0047.bootstrap_plugin_releases,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.AddField(
            model_name='sharedqa',
            name='content_language',
            field=models.CharField(blank=True, default='', help_text='Language used by the generated Q&A content.', max_length=16),
        ),
        migrations.RunPython(
            code=_migration_0048.populate_content_languages,
            reverse_code=django.db.migrations.operations.special.RunPython.noop,
        ),
        migrations.RemoveIndex(
            model_name='sharedqa',
            name='lens_sharedqa_list_idx',
        ),
        migrations.AddIndex(
            model_name='sharedqa',
            index=models.Index(fields=['assistant', 'content_language', 'is_listed', 'status', '-published_at'], name='lens_sharedqa_list_idx'),
        ),
        migrations.DeleteModel(
            name='PluginRelease',
        ),
        migrations.RunPython(
            code=_migration_0050.create_datasource_history_index,
            reverse_code=_migration_0050.drop_datasource_history_index,
            atomic=False,
        ),
        migrations.CreateModel(
            name='DataSourceItem',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('source_type', models.CharField(max_length=32)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('storage_key', models.CharField(max_length=500)),
                ('status', models.CharField(default='active', max_length=16)),
                ('current_version', models.CharField(blank=True, default='', max_length=64)),
                ('datasource', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='lens.datasource')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('datasource', 'storage_key'), name='lens_ds_item_storage_key_unique')],
            },
        ),
        migrations.CreateModel(
            name='DataSourceVersion',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('version', models.CharField(max_length=64)),
                ('storage_key', models.CharField(max_length=500)),
                ('status', models.CharField(default='ready', max_length=16)),
                ('checksum', models.CharField(blank=True, default='', max_length=128)),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='versions', to='lens.datasourceitem')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('item', 'version'), name='lens_ds_item_version_unique')],
            },
        ),
        migrations.CreateModel(
            name='AssistantDataSourceBinding',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('mount_name', models.CharField(max_length=120)),
                ('required', models.BooleanField(default=True)),
                ('assistant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasource_bindings', to='lens.assistant')),
                ('datasource', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.datasource')),
                ('item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='lens.datasourceitem')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('assistant', 'mount_name'), name='lens_assistant_datasource_mount_unique')],
            },
        ),
        migrations.CreateModel(
            name='SessionDataSource',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('datasource_version', models.CharField(blank=True, default='', max_length=64)),
                ('mount_name', models.CharField(max_length=120)),
                ('storage_key', models.CharField(max_length=500)),
                ('datasource', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='lens.datasource')),
                ('item', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='lens.datasourceitem')),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='datasource_snapshots', to='lens.session')),
                ('version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='session_snapshots', to='lens.datasourceversion')),
            ],
        ),
        migrations.AddField(
            model_name='lensnode',
            name='last_metrics',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='lensnode',
            name='active_datasource_operations',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
