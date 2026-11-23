---
status: current
owner: knowledge-service
last_verified: 2026-11-22
verified_against: "9e44c73 (hermes-custom) + 2026-11-22 working tree"
---

# Context Map

## Contexts

- [Knowledge](./CONTEXT.md) - turns WeChat/Hermes inputs into a sourced workflow: Capture or Reading Session, reviewable Draft, confirmed durable artifact, and later cited reuse through Knowledge Cards, Source Maps, and Agent Result Envelopes.
- [Life Assistant](./contexts/life/CONTEXT.md) - tracks life rhythm, reminders, sitting behavior, sleep, meals, and exercise.

## Relationships

- **Knowledge and Life Assistant share Hermes delivery**: both contexts may return Agent Result Envelopes to the Hermes gateway, but their domain objects stay separate.
- **Agent Result Envelope is shared Hermes infrastructure**: it is a delivery contract, not a Knowledge domain object and not a domain relationship between Knowledge and Life Assistant.
- **Knowledge does not own life events**: Life Assistant records personal rhythm and lifestyle events; Knowledge records source material, notes, themes, and viewpoints.
- **Life Assistant does not read Knowledge data in the first version**: lifestyle reviews use Life Assistant records only, not Knowledge Files, Capture Notes, or Knowledge activity.
- **Knowledge does not read Life Assistant data in the first version**: questions about lifestyle state should route to Life Assistant; questions about source material, notes, themes, and viewpoints should route to Knowledge.
- **Life Assistant does not create knowledge artifacts**: first-version records stay in the Life Assistant context and do not promote into the Knowledge context.
- **Separate capability repositories under one Runtime**: Knowledge (`hermes-custom`) and Life Assistant (`hermes-life`, optional) live in separate repositories and do not depend on each other; shared Hermes delivery is infrastructure, not a domain relationship between the two systems.
- **Legacy life tables in hermes-custom are residual data**: any `life_*` tables found in `<repos>/hermes-custom/data/captures.db` are split-repository leftovers, not evidence that Knowledge owns Life Assistant data.
- **The independent Life Assistant store is outside Knowledge reset scope**: `<repos>/hermes-life/data/life.db` is not cleared by the Knowledge local data reset.
