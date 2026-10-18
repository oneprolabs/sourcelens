# Native notification targets

SourceLens exposes `feishu`, `wecom`, and `dingtalk` as independent `channel_type` values in the existing channel API. Email and existing Webhook/app channels retain their contracts. New UI targets offer the three platforms and email; existing Webhooks remain editable.

The SourceLens adapter owns native type validation, rich rendering and sending. It stores native values in the existing notifier channel/record string columns and reuses channel UUIDs and record foreign keys. No dependency installation patches, new tables or migrations are required. The external package's model choices are not extended globally; native writes use SourceLens serializers, and must not use the package's generic `send_notification` entry point or its model-choice validation.

Native alert sends use the existing durable alert outbox, current-subscription checks, occurrence/destination deduplication, cooldown and retry leases. Generic notifier merge/silence settings remain available for legacy Webhook channels; native channels use alert rule policies. A provider acknowledgement is required before a delivery becomes sent. An ambiguous network timeout can still cause duplicate external delivery on retry because robot APIs do not offer idempotency keys.

Feishu receives an interactive card with a semantic header; WeCom and DingTalk receive native Markdown. Titles, event fields, diagnostic summaries, causes and recommendations share the existing recipient-language content. Platform rendering escapes user-controlled Markdown, HTML and mentions and bounds UTF-8 content (WeCom below 4096 bytes, DingTalk below 20 KB, Feishu below 30 KB). Truncation is indicated with an ellipsis. Emails retain plain text. Existing supported Webhooks receive the richer payload too.

Robot URLs and signing secrets are write-only in native channel responses. Blank edits retain saved values. Native endpoints require official HTTPS robot URLs, disallow redirects and use bounded timeouts. Feishu signs the payload; DingTalk signs query parameters; WeCom authenticates through the robot URL key. Records contain unsigned content, locale, target identity and provider result codes, without robot URLs, signing material or exception strings containing credentials. Test sends use the same renderer and sender, create records and are explicit admin actions.

Reference contracts:

- Feishu: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
- WeCom: https://developer.work.weixin.qq.com/document/path/91770
- DingTalk: https://open.dingtalk.com/document/robots/custom-robot-access

Validation uses mocked HTTP transport and browser UI acceptance. Actual delivery/rendering in each platform's desktop/mobile client requires a configured robot and is not implied by local tests.
