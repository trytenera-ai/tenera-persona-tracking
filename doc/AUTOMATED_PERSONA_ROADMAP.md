# Automated Persona Capture Roadmap

This document captures the future implementation plan for making TPT as automated as possible while preserving the currently working dashboard, event ingestion, and session replay behavior.

The core design principle is to separate:

- **User profile**: stable background/context about who the user is. This is used to generate or enrich the persona.
- **Behavioral memory**: time-series actions, sessions, friction, and intent. This is generated from events and replays.
- **Evidence**: event IDs, session IDs, timestamps, URLs, and source metadata supporting every generated claim.

Do **not** ship all phases at once. Each phase should be independently testable, reversible, and safe behind configuration or feature flags where appropriate.

## What we can learn from PostHog-style patterns

| Pattern | Behavioral data collected | Profile/context data collected | How TPT can use it |
| --- | --- | --- | --- |
| Autocapture | Page views, clicks, form submits, timestamps, URLs, referrers, UTM params | Browser/device/viewport context | Build behavioral memories such as visited pricing, clicked invite, returned repeatedly, or abandoned onboarding. |
| Session replay | Interaction sequence, DOM snapshots, scrolls, hesitation, dead clicks, repeated clicks | Session-level URL and device context | Summarize friction and workflows with evidence-backed session IDs. |
| Person profiles | Stable `distinct_id`, properties, aliases | Role, plan, company, lifecycle stage, account metadata | Generate the durable persona profile separate from event memory. |
| Identify / alias | Anonymous-to-known journey | Known user ID and optional traits | Preserve pre-login behavior when the user later becomes known. |
| Event properties | Per-event metadata | Page, project, feature, CTA, source | Improve memory quality and make each behavioral claim explainable. |
| Error tracking | Exceptions, console/network failures | Affected route/browser/session | Add friction memories such as hit setup error or failed checkout. |
| Funnels / paths | Common journeys and drop-offs | Cohort-level path patterns | Discover behavioral segments such as onboarding drop-offs or pricing evaluators. |
| Cohorts | Rule-based segments | Segment membership | Seed generated personas from consistent behavior/profile rules. |
| Optional zero-code surveys | Direct answers and free text | Intent, job-to-be-done, role, pains | Add high-confidence profile facts without requiring app code changes. |
| AI summaries | Session/event interpretation | Derived traits with confidence | Generate evidence-backed persona drafts and behavioral memories asynchronously. |

## Phase 1 — Safe data model and terminology cleanup

Goal: make the profile/memory split explicit without changing current runtime behavior.

Checklist:

- [ ] Define canonical data layers:
  - `profile`: stable user/account facts.
  - `memory`: time-series behavioral summaries.
  - `evidence`: raw event/session references backing generated facts.
  - `derived_persona`: generated persona output built from profile + memory.
- [ ] Document which fields belong in each layer.
- [ ] Add migration-safe schema plans only if current tables are insufficient.
- [ ] Keep existing event ingestion, dashboard, and session replay behavior unchanged.
- [ ] Add regression tests covering current dashboard endpoints before any behavior changes.

Acceptance criteria:

- Existing `/api/v1/track`, `/api/v1/personas`, `/api/v1/logs/*`, and `/api/v1/sessions/*` behavior remains compatible.
- Future contributors can tell whether a new field belongs to profile, memory, or evidence.

## Phase 2 — Autocapture normalization and privacy guardrails

Goal: make header-only capture more useful and safer without requiring customers to manually instrument events.

Checklist:

- [ ] Standardize event names:
  - `page_view`
  - `click`
  - `form_submit`
  - `session_start`
  - `session_end`
  - `error`
- [ ] Standardize event properties:
  - `url`, `path`, `title`, `referrer`
  - `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`
  - `element_text`, `element_tag`, `element_role`, `href`
  - `viewport_width`, `viewport_height`, `device_type`
  - `session_id`
- [ ] Do not collect form/input values by default.
- [ ] Mask or block sensitive fields by default:
  - password inputs
  - credit card fields
  - SSN-like fields
  - email/phone fields unless explicitly allowed
  - fields marked with `data-tpt-mask` or equivalent
- [ ] Add opt-in allowlist support for safe custom properties.
- [ ] Add tests for event schema normalization and sensitive data blocking.

Acceptance criteria:

- Header-only install produces predictable, typed events.
- Sensitive values are not captured by default.
- Existing manual tracking still works.

## Phase 3 — Identity, aliasing, and profile enrichment

Goal: connect anonymous behavior to known users and build stable user profiles.

Checklist:

- [ ] Add or formalize an `identify` endpoint/client API.
- [ ] Support anonymous-to-known merge:
  - anonymous `distinct_id` remains as historical evidence.
  - known `distinct_id` becomes the stable user identity.
  - prior events/sessions are preserved.
- [ ] Store stable profile facts separately from behavioral memory.
- [ ] Track source and confidence for profile facts:
  - explicit identify call
  - survey answer
  - imported account metadata
  - inferred value
- [ ] Avoid overwriting high-confidence explicit facts with low-confidence inferred facts.
- [ ] Add tests for anonymous history preservation and profile conflict behavior.

Acceptance criteria:

- A pre-login anonymous session can be linked to a later known user.
- Profile facts remain explainable by source.
- Persona generation can consume stable profile facts without mixing in raw event noise.

## Phase 4 — Behavioral memory generation

Goal: convert raw behavior into concise memory records asynchronously, not in the request path.

Checklist:

- [ ] Add background job or scheduled worker for memory generation.
- [ ] Summarize events/session replay into short behavioral memories.
- [ ] Store each memory with:
  - summary text
  - confidence
  - evidence event IDs/session IDs
  - time range
  - generated model/version
- [ ] Include friction signals:
  - rage clicks
  - dead clicks
  - repeated visits
  - long dwell time
  - form abandonment
  - errors
- [ ] Include positive signals:
  - completed onboarding
  - repeated feature usage
  - successful conversion path
- [ ] Add manual/debug view to inspect memory evidence.
- [ ] Add tests for idempotency and evidence preservation.

Acceptance criteria:

- Memories can be regenerated without duplicating records.
- Every generated memory has evidence.
- Dashboard and capture endpoints remain fast because generation is async.

## Phase 5 — Persona generation from profile + memory

Goal: generate persona drafts using stable profile facts plus behavioral memory, not raw events alone.

Checklist:

- [ ] Define persona generation input contract:
  - stable profile facts
  - recent/high-confidence memories
  - cohort/cluster information
  - evidence references
- [ ] Generate persona fields:
  - name/label
  - intent/job-to-be-done
  - needs
  - frictions
  - behavioral traits
  - confidence score
  - evidence references
- [ ] Store generated persona separately from raw profile and memory.
- [ ] Provide regenerate/rollback behavior.
- [ ] Do not auto-overwrite customer-provided persona fields without explicit approval.
- [ ] Add tests for deterministic output shape and evidence references.

Acceptance criteria:

- Persona output combines who the user is with what they did.
- Persona claims are evidence-backed.
- Generated content is reversible and inspectable.

## Phase 6 — Optional zero-code surveys

Goal: improve profile quality without requiring customers to add custom app code.

Checklist:

- [ ] Add dashboard survey config:
  - question
  - response type
  - display path rules
  - timing rules
  - audience rules
  - frequency limits
- [ ] Have the existing snippet fetch active survey config.
- [ ] Render lightweight in-app survey UI from the snippet.
- [ ] Store responses as high-confidence profile facts or memories depending on question type.
- [ ] Add controls for opt-out, rate limiting, and user dismissal.
- [ ] Add tests for survey targeting and response ingestion.

Acceptance criteria:

- Customers can ask a visitor question without writing app code.
- Survey responses feed the profile/memory system with explicit source metadata.
- Surveys are off by default and customer-controlled.

## Recommended implementation order

1. Add regression tests around current dashboard, personas, logs, sessions, and filtering.
2. Implement Phase 1 documentation/schema definitions only.
3. Implement Phase 2 normalization with privacy guardrails behind config.
4. Implement Phase 3 identify/alias support.
5. Implement Phase 4 async memory generation.
6. Implement Phase 5 persona generation.
7. Implement Phase 6 optional surveys.

## Non-goals for the first implementation pass

- Do not immediately add all AI generation into the live dashboard.
- Do not capture sensitive form fields by default.
- Do not block event ingestion on AI calls.
- Do not overwrite explicit profile/persona fields with inferred values.
- Do not deploy automatically without manual review.

## Example target output

```json
{
  "profile": {
    "role": "Product Manager",
    "company_size": "50-200",
    "plan": "trial",
    "source": "organic search"
  },
  "memory": [
    {
      "summary": "Visited pricing page three times over two days.",
      "confidence": 0.86,
      "evidence": { "event_ids": ["evt_1", "evt_2", "evt_3"] }
    },
    {
      "summary": "Started onboarding but dropped off at the invite step.",
      "confidence": 0.78,
      "evidence": { "session_ids": ["sess_1"] }
    }
  ],
  "derived_persona": {
    "label": "Evaluation-focused PM",
    "intent": "Assessing whether the product fits team workflow.",
    "frictions": ["unclear setup", "pricing uncertainty"],
    "confidence": 0.82
  }
}
```
