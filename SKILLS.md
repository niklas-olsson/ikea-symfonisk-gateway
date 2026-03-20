# Agent Skills

This document catalogs available agent skills for this codebase.

## Available Skills

| Skill | Description | Location |
|-------|-------------|----------|
| `wave-organizer` | Transform plans into wave-based GitHub issues | [`skills/wave-organizer/SKILL.md`](./skills/wave-organizer/SKILL.md) |

## Wave Organizer

### What It Does

Transforms user plans into structured GitHub issues organized into waves:

1. **Parse** the plan into atomic tasks
2. **Classify** tasks by dependencies (wave 1 = parallelizable, wave N = depends on waves 1..N-1)
3. **Auto-detect** component labels (`core`, `renderer`, `ingress`, `ui`) from file paths
4. **Create** issues via `gh issue create`
5. **Verify** dependency graph has no circular references

### How to Trigger

Invoke the skill by saying:

```
Use the wave-organizer skill to break down this plan...
```

Or simply describe your plan and mention organizing it:

```
I want to add user authentication with OAuth2.
Can you use wave-organizer to create the issues for this?

Plan:
1. Add JWT token generation (no deps)
2. Add login endpoint (depends on #1)
3. Add user registration (no deps)
4. Add admin dashboard (depends on #2)
```

### Trigger Phrases

- "Use wave-organizer to..."
- "Organize this plan into waves..."
- "Break down this roadmap into issues..."
- "Create wave-based issues for..."
- "Plan this feature into workstreams..."

### Wave Naming Convention

Issues follow: `[Wave N] Component - Feature Name`

Labels used:
- `wave-1`, `wave-2`, etc.
- `core`, `renderer`, `ingress`, `ui`
