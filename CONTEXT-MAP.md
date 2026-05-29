# Context Map

## Contexts

- [Knowledge](./CONTEXT.md) - turns WeChat/Hermes inputs into durable knowledge objects such as Capture Notes, Theme Pages, Garden Cards, Source Maps, and Agent Result Envelopes.
- [Life Assistant](./contexts/life/CONTEXT.md) - tracks life rhythm, reminders, sitting behavior, sleep, meals, exercise, reflection, and lightweight long-term decisions.

## Relationships

- **Knowledge and Life Assistant share Hermes delivery**: both contexts may return Agent Result Envelopes to the Hermes gateway, but their domain objects stay separate.
- **Knowledge does not own life events**: Life Assistant records personal rhythm and lifestyle events; Knowledge records source material, notes, themes, and viewpoints.
- **Life Assistant does not automatically create knowledge artifacts**: first-version reflections and decisions stay in the Life Assistant context unless the user later asks to promote them into the Knowledge context.
