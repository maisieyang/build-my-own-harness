---
id: 01HEXAMPLE000
name: stripe-sdk-version
description: This project uses Stripe SDK 8.x with the legacy API
type: project
scope: private
created_at: 2026-05-26T10:00:00+00:00
updated_at: 2026-05-26T10:00:00+00:00
importance: 0.7
tags:
  - payment
  - stripe
---

The Stripe integration in this project pins to SDK version 8.x and
uses the legacy API surface (pre-2024 redesign). Key implications:

- `stripe.Charge` is preferred over `stripe.PaymentIntent` for most
  flows — the team chose to defer migration.
- Webhook signature verification uses the legacy `stripe.Webhook.construct_event`
  shape with the `tolerance` parameter set explicitly (default of
  300s is too tight for our async processing).
- Refunds must include `metadata.original_charge_id` for our audit
  trail — the `stripe.Refund.create` call wrapper enforces this.

When debugging Stripe issues, check `src/payments/stripe_client.py`
first — it's the single integration point.
