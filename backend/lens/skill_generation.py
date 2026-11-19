from pathlib import Path

from .llm import run_completion
from .models import AssistantSkill, GlobalSetting, Skill

BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
WORKSPACE_GUIDE_KIND = "workspace_guide"
WORKSPACE_GUIDE_LOAD_CONFIG = {
    "mode": "context",
    "inject": True,
}

SKILL_GENERATOR_MODEL_KEY = "lens.skills.generator_model_ref"
SKILL_BEAUTIFY_NODE = "lens.skill_beautify"
SKILL_BEAUTIFY_SYSTEM_HEADER = (
    "You are an expert SKILL.md author. Rewrite the user's draft into a "
    "single, well-structured SKILL.md that follows the authoring guide "
    "below. Keep it concise and faithful to the draft's intent: improve "
    "structure, clarity, and formatting while preserving every concrete "
    "fact, path, command, and rule. If the draft is empty, produce a "
    "sensible starting point from the skill name. Respond with ONLY the "
    "final SKILL.md in Markdown — no explanations and no surrounding code "
    "fences.\n\n===== SKILL AUTHORING GUIDE =====\n"
)


class SkillGeneratorNotConfigured(Exception):
    """Raised when no skill generator model is configured."""


def read_builtin_skill(skill_name):
    """Return the bundled SKILL.md content for a built-in skill."""

    path = BUILTIN_SKILLS_DIR / skill_name / "SKILL.md"
    return path.read_text(encoding="utf-8")


def skill_generator_model_ref():
    """Return the configured skill generator model ref, or empty string."""

    setting = GlobalSetting.objects.filter(
        key=SKILL_GENERATOR_MODEL_KEY
    ).first()
    value = setting.value if setting else ""
    return value.strip() if isinstance(value, str) else ""


def _strip_code_fence(text):
    """Strip a single wrapping ```` ``` ```` fence if the model added one."""

    out = (text or "").strip()
    if not out.startswith("```"):
        return out
    lines = out.split("\n")[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def beautify_skill_content(*, content, name="", user_id=None):
    """Polish a draft SKILL.md through the configured generator model.

    Raises SkillGeneratorNotConfigured when no generator model is set.
    """

    model_ref = skill_generator_model_ref()
    if not model_ref:
        raise SkillGeneratorNotConfigured()

    system = SKILL_BEAUTIFY_SYSTEM_HEADER + read_builtin_skill("skill-creator")
    label = (name or "").strip()
    draft = (content or "").strip()
    user = f"Skill name: {label}\n\n" if label else ""
    user += "Draft SKILL.md:\n\n" + (
        draft or "(empty — generate a sensible starting point)"
    )

    result = run_completion(
        model_ref=model_ref,
        system=system,
        user=user,
        node_name=SKILL_BEAUTIFY_NODE,
        user_id=user_id,
        platform_model=True,
    )
    return _strip_code_fence(result.content)


def sync_workspace_guide_skill(assistant, workspace_guide):
    """Persist an assistant Workspace Guide and disable legacy bindings."""

    if workspace_guide is None:
        return None

    enabled = bool(workspace_guide.get("enabled"))
    content = str(workspace_guide.get("content") or "").strip()
    assistant.workspace_guide = content if enabled else ""
    assistant.save(update_fields=["workspace_guide", "updated_at"])
    _disable_workspace_guide_binding(assistant)
    return content


def build_workspace_guide_skill_md(*, content):
    """Return the user-provided workspace context as-is (no template)."""

    return content.strip()


def get_workspace_guide_payload(assistant):
    """Return the persisted Workspace Guide payload for an assistant."""

    if assistant.workspace_guide:
        return {
            "enabled": True,
            "content": assistant.workspace_guide,
        }

    binding = (
        assistant.skill_bindings.select_related("skill")
        .filter(skill__kind=WORKSPACE_GUIDE_KIND)
        .first()
    )
    if binding is None:
        return {
            "enabled": False,
            "content": "",
        }
    return {
        "enabled": binding.enabled,
        "content": _workspace_guide_user_content(binding.skill.definition),
        "skill_uuid": str(binding.skill.uuid),
        "load_config": binding.load_config,
    }


def _disable_workspace_guide_binding(assistant):
    """Disable an existing Workspace Guide binding if one exists."""

    AssistantSkill.objects.filter(
        assistant=assistant,
        skill__kind=WORKSPACE_GUIDE_KIND,
    ).update(enabled=False)


def _workspace_guide_user_content(definition):
    """Extract the user-authored workspace guide section."""

    if isinstance(definition, str):
        text = definition
    else:
        text = (
            definition.get("content")
            or definition.get("markdown")
            or definition.get("skill_md")
            or ""
        )
    marker = "## Workspace Structure"
    rules_marker = "## Required Usage Rules"
    if marker not in text:
        return text.strip()
    section = text.split(marker, 1)[1]
    if rules_marker in section:
        section = section.split(rules_marker, 1)[0]
    return section.strip()
