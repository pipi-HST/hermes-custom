# Context Map

## Contexts

- [Knowledge](./CONTEXT.md) - turns WeChat/Hermes inputs into durable knowledge objects such as Capture Notes, Theme Pages, Garden Cards, Source Maps, and Agent Result Envelopes.
- [Life Assistant](./contexts/life/CONTEXT.md) - tracks life rhythm, reminders, sitting behavior, sleep, meals, exercise, reflection, and lightweight long-term decisions.

## Relationships

- **Knowledge and Life Assistant share Hermes delivery**: both contexts may return Agent Result Envelopes to the Hermes gateway, but their domain objects stay separate.
- **Agent Result Envelope is shared Hermes infrastructure**: it is a delivery contract, not a Knowledge domain object and not a domain relationship between Knowledge and Life Assistant.
- **Knowledge does not own life events**: Life Assistant records personal rhythm and lifestyle events; Knowledge records source material, notes, themes, and viewpoints.
- **Life Assistant does not read Knowledge data in the first version**: lifestyle reviews use Life Assistant records only, not Obsidian notes, Capture Notes, or Knowledge activity.
- **Knowledge does not read Life Assistant data in the first version**: questions about lifestyle state should route to Life Assistant; questions about source material, notes, themes, and viewpoints should route to Knowledge.
- **Life Assistant does not create knowledge artifacts**: first-version reflections and decisions stay in the Life Assistant context and do not promote into the Knowledge context.
- **Code may share a repository, but the contexts should not depend on each other**: shared Hermes delivery is infrastructure; it is not a domain relationship between the two systems.
