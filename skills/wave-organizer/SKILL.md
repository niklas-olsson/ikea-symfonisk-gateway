---
name: wave-organizer
description: Transform user plans into structured GitHub issues with wave-based workstreams and dependency management
---

# Wave Organizer Skill

Act as an ad-hoc organizer/product manager when users present plans for implementation work. Transform plans into executable workstreams using GitHub issues.

## Core Workflow

### Step 1: Parse the Plan

Extract atomic tasks from the user's plan. Each task should be:
- **Specific**: One clear action
- **Independent**: Can be described without reference to other tasks
- **Verifiable**: Has clear completion criteria

### Step 2: Analyze Dependencies

For each task, identify what it depends on:
- Other tasks in the same plan
- Existing code/features in the codebase
- External systems or libraries

### Step 3: Classify into Waves

Assign each task to a wave based on dependencies:

| Wave | Criteria |
|------|----------|
| Wave 1 | No dependencies (fully parallelizable) |
| Wave 2 | Only depends on Wave 1 |
| Wave 3 | Depends on Wave 1-2 |
| Wave N | Depends on Wave 1-(N-1) |

### Step 4: Assign Component Labels

Auto-detect component from file paths/packages mentioned:

| Path Pattern | Label |
|--------------|-------|
| `bridge_core/` | `core` |
| `renderer_sonos/` | `renderer` |
| `adapters/ingress/` or `ingress_sdk/` | `ingress` |
| `ui_web/` | `ui` |
| Mixed or unclear | `core` |

### Step 5: Create Issues

Use `gh issue create` with proper labels:

```bash
gh issue create \
  --title "[Wave N] Component - Feature Name" \
  --body "$(cat ISSUE_TEMPLATE.md filled with task details)" \
  --label "wave-N" \
  --label "<component>"
```

### Step 6: Verify Wave Completeness

After creating all issues:

1. List all issues: `gh issue list --label "wave-1"`
2. For each wave, verify that all dependencies are satisfied by previous waves
3. If a dependency points to a non-existent issue, flag it for resolution
4. Report any circular dependencies

## Issue Template Structure

Use this format for each issue (see ISSUE_TEMPLATE.md for full template):

```
## Context
Brief explanation of what this feature does and why it matters.

## Current State
What exists today that this work builds upon.
- Existing files, stubs, or related functionality

## Deliverables
- [ ] Specific task 1
- [ ] Specific task 2
- [ ] Specific task 3

## Dependencies
Issue #XX (Title) - if any
Issue #YY (Title) - if any

## Verification
How to verify this work is complete.
```

## Naming Convention

Issue titles follow: `[Wave N] Component - Brief Feature Description`

Examples:
- `[Wave 1] Core - Event Bus & Config Store`
- `[Wave 2] Ingress - Synthetic Source Adapter`
- `[Wave 3] Core - REST API Endpoints`

## Wave Assignment Rules

### Automatic Classification

```python
def assign_wave(task, all_tasks):
    deps = get_all_dependencies(task)  # direct + transitive
    if not deps:
        return 1
    
    max_wave = 0
    for dep in deps:
        dep_wave = get_wave(dep)
        max_wave = max(max_wave, dep_wave)
    return max_wave + 1
```

### Conflict Resolution

If a task has conflicting dependencies:
1. Identify the longest dependency chain
2. Assign to the wave after that chain
3. Document the dependency explicitly

If circular dependencies are detected:
1. Stop and report the cycle
2. Ask user to clarify which dependency should be broken
3. Do not create issues until resolved

## Example

User provides:
> I want to add:
> 1. Authentication middleware (depends on nothing)
> 2. User service (depends on #1)
> 3. Admin dashboard (depends on #2)
> 4. Analytics API (depends on #1)

Output:
- Wave 1: Authentication middleware
- Wave 2: User service
- Wave 3: Admin dashboard
- Wave 2: Analytics API (parallel with User service)

## Quality Checklist

Before finalizing:
- [ ] All tasks have been converted to issues
- [ ] Wave labels are assigned correctly
- [ ] Component labels are assigned correctly
- [ ] All dependencies reference existing or newly created issues
- [ ] No circular dependencies exist
- [ ] Each issue has clear deliverables and verification steps
