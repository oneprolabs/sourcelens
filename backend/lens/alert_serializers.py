"""Validated contracts for managed alerts and personal subscriptions."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from rest_framework import serializers

from .alerts import RUNNING_EVENTS, SUBJECT_EVENTS, personal_subscription_allowed
from .models import AlertDelivery, AlertEvent, AlertRule, AlertSubscription, Assistant, DataSource, LensNode


def validate_channel(value, email_only=False):
    """Resolve an enabled supported channel without exposing its configuration."""

    from agentcore_notifier.adapters.django.models import NotificationChannel

    channel = NotificationChannel.objects.filter(uuid=value, is_active=True).first()
    supported = {"email"} if email_only else {"email", "webhook"}
    if not channel or channel.channel_type not in supported:
        raise serializers.ValidationError("Select an active supported notification channel.")
    return channel


class AlertSubscriptionSerializer(serializers.ModelSerializer):
    """Admin-managed destinations; personal subscribers are managed separately."""

    uuid = serializers.UUIDField(required=False)
    email_recipients = serializers.ListField(child=serializers.EmailField(), required=False)
    users = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.filter(is_active=True), many=True, required=False
    )
    groups = serializers.PrimaryKeyRelatedField(queryset=Group.objects.all(), many=True, required=False)

    class Meta:
        model = AlertSubscription
        fields = ["uuid", "name", "enabled", "channel_uuid", "email_recipients", "users", "groups"]

    def validate(self, attrs):
        """Require a destination and recipients for email transports."""

        channel = validate_channel(attrs.get("channel_uuid"))
        if channel.channel_type == "email" and not any(
            attrs.get(key) for key in ("email_recipients", "users", "groups")
        ):
            raise serializers.ValidationError({"email_recipients": "Select at least one recipient, user or group."})
        return attrs


class AlertRuleSerializer(serializers.ModelSerializer):
    """Rules with typed scopes, conditions and independently managed destinations."""

    topic = serializers.RegexField(r"^[a-z][a-z0-9_.-]{0,119}$", required=False, allow_blank=True)
    events = serializers.ListField(child=serializers.ChoiceField(choices=AlertRule.Event.choices), allow_empty=False)
    assistants = serializers.SlugRelatedField(
        many=True, slug_field="uuid", queryset=Assistant.objects.all(), required=False
    )
    datasources = serializers.SlugRelatedField(
        many=True, slug_field="uuid", queryset=DataSource.objects.all(), required=False
    )
    lensnodes = serializers.SlugRelatedField(
        many=True, slug_field="uuid", queryset=LensNode.objects.all(), required=False
    )
    subscriptions = AlertSubscriptionSerializer(many=True, required=False)
    created_by = serializers.CharField(source="created_by.username", read_only=True)
    email_recipients = serializers.ListField(child=serializers.EmailField(), required=False)

    class Meta:
        model = AlertRule
        fields = [
            "uuid",
            "name",
            "subject",
            "topic",
            "enabled",
            "events",
            "evaluation_mode",
            "duration_threshold",
            "token_threshold",
            "rounds_threshold",
            "cooldown_seconds",
            "assistants",
            "datasources",
            "lensnodes",
            "personal_subscription_enabled",
            "personal_channel_uuid",
            "subscriptions",
            "channel_uuid",
            "email_recipients",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_by", "created_at", "updated_at"]

    def to_representation(self, instance):
        """Keep personal ownership data out of the managed-destination editor."""

        data = super().to_representation(instance)
        data["subscriptions"] = AlertSubscriptionSerializer(
            instance.subscriptions.filter(personal=False), many=True
        ).data
        return data

    def validate(self, attrs):
        """Reject incompatible event/scope combinations and nonpositive thresholds."""

        def value(key, default=None):
            return attrs.get(key, getattr(self.instance, key, default) if self.instance else default)

        name = str(value("name", "")).strip()
        if not name:
            raise serializers.ValidationError({"name": "Name is required."})
        attrs["name"] = name
        subject, events = value("subject", "run"), set(value("events", []))
        if subject == "message" and not value("topic", ""):
            raise serializers.ValidationError({"topic": "A message topic is required."})
        if not events or events - SUBJECT_EVENTS[subject]:
            raise serializers.ValidationError({"events": "Choose events belonging to the selected subject."})
        if value("evaluation_mode", "terminal") == "running" and (subject != "run" or events - RUNNING_EVENTS):
            raise serializers.ValidationError({"events": "Running reminders support usage, rounds and duration only."})
        for event, field in (
            ("token_exceeded", "token_threshold"),
            ("rounds_exceeded", "rounds_threshold"),
            ("run_slow", "duration_threshold"),
        ):
            if event in events and (not value(field) or value(field) <= 0):
                raise serializers.ValidationError({field: "A positive threshold is required."})
        for relation in ("assistants", "datasources"):
            scope = attrs.get(relation)
            if scope is None and self.instance:
                scope = getattr(self.instance, relation).all()
            if scope and (subject == "message" or ((subject == "run") != (relation == "assistants"))):
                raise serializers.ValidationError({relation: "Scope does not match the selected subject."})
        lensnodes = attrs.get("lensnodes")
        if lensnodes is None and self.instance:
            lensnodes = self.instance.lensnodes.all()
        if subject == "message" and lensnodes:
            raise serializers.ValidationError({"lensnodes": "Message topics do not use resource scopes."})
        if value("personal_subscription_enabled", False):
            if subject not in {"run", "message"}:
                raise serializers.ValidationError(
                    {"personal_subscription_enabled": "Datasource subscriptions require admin access."}
                )
            validate_channel(value("personal_channel_uuid"), email_only=True)
        channel_uuid = value("channel_uuid")
        if channel_uuid:
            channel = validate_channel(channel_uuid)
            if channel.channel_type == "email" and not value("email_recipients", []):
                raise serializers.ValidationError({"email_recipients": "Email recipients are required."})
        subscriptions = attrs.get("subscriptions", [])
        seen = set()
        for subscription in subscriptions:
            identifier = subscription.get("uuid")
            if identifier and (
                identifier in seen
                or not self.instance
                or not self.instance.subscriptions.filter(uuid=identifier, personal=False).exists()
            ):
                raise serializers.ValidationError(
                    {"subscriptions": "Destination does not belong to this rule or is duplicated."}
                )
            seen.add(identifier)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """Persist a rule and its destination set atomically."""

        subscriptions = validated_data.pop("subscriptions", None)
        rule = super().create(validated_data)
        self._save_subscriptions(rule, subscriptions)
        return rule

    @transaction.atomic
    def update(self, instance, validated_data):
        """Replace managed destinations while preserving personal subscriptions."""

        subscriptions = validated_data.pop("subscriptions", None)
        legacy_changed = "channel_uuid" in validated_data or "email_recipients" in validated_data
        instance = super().update(instance, validated_data)
        self._save_subscriptions(instance, subscriptions, legacy_changed=legacy_changed)
        return instance

    def _save_subscriptions(self, rule, rows, legacy_changed=True):
        """Retain destination identities so delivery history and cooldown survive edits."""

        if rows is None:
            if not legacy_changed:
                return
            if not rule.channel_uuid:
                rule.subscriptions.filter(personal=False, name="Legacy destination").delete()
            else:
                destination, _ = rule.subscriptions.get_or_create(personal=False, name="Legacy destination")
                destination.channel_uuid = rule.channel_uuid
                destination.email_recipients = rule.email_recipients
                destination.save()
            return
        retained = []
        for row in rows:
            row = dict(row)
            identifier = row.pop("uuid", None)
            users, groups = row.pop("users", []), row.pop("groups", [])
            destination = (
                rule.subscriptions.get(uuid=identifier, personal=False) if identifier else AlertSubscription(rule=rule)
            )
            for key, value in row.items():
                setattr(destination, key, value)
            destination.save()
            destination.users.set(users)
            destination.groups.set(groups)
            retained.append(destination.pk)
        rule.subscriptions.filter(personal=False).exclude(pk__in=retained).delete()


class AlertDeliverySerializer(serializers.ModelSerializer):
    """Expose provider acceptance separately from pending and suppressed work."""

    class Meta:
        model = AlertDelivery
        fields = ["uuid", "destination", "status", "attempts", "next_attempt_at", "sent_at", "error_message"]
        read_only_fields = fields


class AlertEventSerializer(serializers.ModelSerializer):
    """Unified occurrence history that survives resource/rule removal."""

    rule_uuid = serializers.UUIDField(source="rule.uuid", read_only=True, allow_null=True)
    rule_name = serializers.SerializerMethodField()
    run_uuid = serializers.UUIDField(source="run.uuid", read_only=True, allow_null=True)
    deliveries = AlertDeliverySerializer(many=True, read_only=True)

    class Meta:
        model = AlertEvent
        fields = [
            "uuid",
            "rule_uuid",
            "rule_name",
            "run_uuid",
            "subject",
            "source_uuid",
            "event_type",
            "detail",
            "status",
            "error_message",
            "created_at",
            "deliveries",
        ]
        read_only_fields = fields

    def get_rule_name(self, obj):
        """Use the occurrence snapshot when a rule has been deleted."""

        return obj.rule_snapshot.get("name") or (obj.rule.name if obj.rule else "")


class PersonalAlertSubscriptionSerializer(serializers.ModelSerializer):
    """Users may only select a published rule and enable their own subscription."""

    rule_uuid = serializers.UUIDField(source="rule.uuid")
    rule_name = serializers.CharField(source="rule.name", read_only=True)

    class Meta:
        model = AlertSubscription
        fields = ["uuid", "rule_uuid", "rule_name", "enabled"]
        read_only_fields = ["uuid", "rule_name"]

    def to_internal_value(self, data):
        """Reject recipient/identity injection instead of silently ignoring it."""

        if set(data) - {"rule_uuid", "enabled"}:
            raise serializers.ValidationError({"detail": "Only rule_uuid and enabled can be changed."})
        return super().to_internal_value(data)

    def validate(self, attrs):
        """Check published rule, current visibility, email and duplicate ownership."""

        user = self.context["request"].user
        identifier = attrs.get("rule", {}).get("uuid")
        rule = AlertRule.objects.filter(uuid=identifier, enabled=True).first() if identifier else self.instance.rule
        if not rule:
            raise serializers.ValidationError({"rule_uuid": "Rule unavailable."})
        candidate = AlertSubscription(rule=rule, user=user, personal=True)
        if not personal_subscription_allowed(candidate):
            raise serializers.ValidationError({"rule_uuid": "Rule unavailable or account email missing."})
        if self.instance and self.instance.rule_id != rule.pk:
            raise serializers.ValidationError({"rule_uuid": "Create a separate subscription for another rule."})
        if not self.instance and AlertSubscription.objects.filter(rule=rule, user=user).exists():
            raise serializers.ValidationError({"rule_uuid": "Already subscribed."})
        attrs["rule"] = rule
        return attrs

    def create(self, validated_data):
        """Bind identity and destination entirely from the authenticated account."""

        return AlertSubscription.objects.create(**validated_data, user=self.context["request"].user, personal=True)


class MessagePublicationSerializer(serializers.Serializer):
    """Bound plain-text publication input with a required business idempotency key."""

    topic = serializers.RegexField(r"^[a-z][a-z0-9_.-]{0,119}$", max_length=120)
    idempotency_key = serializers.CharField(max_length=160)
    title = serializers.RegexField(r"^[^\r\n]+$", max_length=200)
    body = serializers.CharField(max_length=12000)


class MessageRecordSerializer(serializers.Serializer):
    """Expose recorded publications without equating matching with external delivery."""

    uuid = serializers.UUIDField(read_only=True)
    source_uuid = serializers.CharField(read_only=True)
    topic = serializers.CharField(source="payload.topic", read_only=True)
    idempotency_key = serializers.CharField(source="payload.idempotency_key", read_only=True)
    title = serializers.CharField(source="payload.title", read_only=True)
    body = serializers.CharField(source="payload.body", read_only=True)
    publisher_name = serializers.CharField(source="payload.publisher_name", read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    processed_at = serializers.DateTimeField(read_only=True, allow_null=True)
