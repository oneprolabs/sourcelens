"""Policy for who may open a shared Q&A link.

The policy is resolved from three layers:

1. the Assistant that owns the share, when it configures one;
2. the deployment-wide GlobalSetting, when it configures one;
3. the publisher's own choice, recorded on the share.

A configured layer decides first, so a deployment or Assistant policy always
applies and the publisher is not asked while one is in place. The publisher
only chooses while neither of them is configured, and the built-in default
keeps public shares open to anonymous viewers.
"""

from lens.models import GlobalSetting

INHERIT = "inherit"
PUBLIC = "public"
LOGIN = "login"

PUBLIC_SHARE_POLICIES = (INHERIT, PUBLIC, LOGIN)

GLOBAL_POLICY_SETTING_KEY = "lens.public_share.policy"
DEFAULT_POLICY = PUBLIC


def normalize_policy(value):
    """Return a known policy value, or None when unset or invalid."""

    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in PUBLIC_SHARE_POLICIES:
            return candidate
    return None


def global_policy():
    """Return the deployment-wide policy, or None when it is not configured."""

    setting = GlobalSetting.objects.filter(
        key=GLOBAL_POLICY_SETTING_KEY
    ).first()
    if setting is None:
        return None
    return normalize_policy(setting.value)


def assistant_policy(assistant):
    """Return the Assistant-level policy, or None when it is not configured."""

    if assistant is None:
        return None
    settings = assistant.settings
    if not isinstance(settings, dict):
        return None
    share_settings = settings.get("public_share")
    if not isinstance(share_settings, dict):
        return None
    return normalize_policy(share_settings.get("policy"))


def share_policy(share):
    """Return the per-share policy, or None when the publisher did not choose."""

    if share is None:
        return None
    value = getattr(share, "require_login", None)
    if value is None:
        return None
    return LOGIN if value else PUBLIC


def configured_policy(assistant=None, share=None):
    """Return the policy the configured layers apply.

    The Assistant policy wins over the deployment policy. The publisher's
    own choice is separate: it is only offered while neither layer decided.
    """

    for candidate in (assistant_policy(assistant), global_policy()):
        if candidate and candidate != INHERIT:
            return candidate
    return None


def policy_source(assistant=None):
    """Return which layer decided the policy, or None when none did."""

    for candidate, source in (
        (assistant_policy(assistant), "assistant"),
        (global_policy(), "global"),
    ):
        if candidate and candidate != INHERIT:
            return source
    return None


def resolve_policy(assistant=None, share=None):
    """Return the effective policy for a share link.

    A configured layer decides first, so enabling a policy in the Assistant
    or the deployment applies to every share it covers. The publisher's own
    choice only applies while neither layer is configured, and the built-in
    default keeps public shares open to anonymous viewers.
    """

    return configured_policy(assistant) or share_policy(share) or DEFAULT_POLICY


def allows_anonymous_access(assistant=None, share=None):
    """Return True when the link may be opened without signing in."""

    return resolve_policy(assistant, share) == PUBLIC


def requires_publisher_choice(assistant=None):
    """Return True when the publisher must choose the policy for a new share.

    The choice is only offered when neither the Assistant nor the deployment
    already decided, and only for a public Assistant whose links a visitor
    could open without signing in.
    """

    if assistant is None:
        return False
    if assistant.visibility != assistant.Visibility.PUBLIC:
        return False
    return configured_policy(assistant) is None
