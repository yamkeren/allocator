# Global FIFO Queue Design

**Date:** 2026-06-12
**Status:** Approved
**Scope:** `manager/allocator_manager/tasks/queue_processor.py` + HANDOFF.md

## Goal

Replace the per-device-set FIFO queue with a single global FIFO across all
PENDING sessions.

## Current behavior

`process_queue` groups PENDING sessions by their sorted requested-device set.
Within each set it walks oldest-first and stops at the first session that still
cannot be satisfied (head-of-line per identical set). Sessions requesting
different device sets are independent and never block each other.

## New behavior

One global queue ordered by `created_at`:

- Each run walks **all** PENDING sessions oldest-first and calls
  `AllocationService.allocate()` on each.
- A session that cannot be satisfied right now is **skipped** — it does not
  block the sessions behind it ("skip unsatisfiable").
- Older sessions are always *attempted* before newer ones, so age is the global
  priority, but a satisfiable newer session may start while an unsatisfiable
  older one waits.

### Ordering property for identical sets

Two sessions requesting the same device set keep their relative order without
any explicit grouping: a failed `allocate()` mutates nothing, so if the older
one fails in a run, the newer one (same requirement, same instant) fails too.

### Starvation: accepted risk

A session wanting a multi-device set can in principle be starved by a stream of
newer sessions taking subsets of its devices. Decision: **no guard**. Session
expiry already bounds total wait, and the system is small-scale. If real-world
starvation appears, add aging escalation later (skip counter; past threshold the
queue runs strict until the starved session starts). Device reservation
(RESERVED device state) was considered and rejected as ~3x scope with a
utilization penalty.

## Code changes

`queue_processor.py` only:

- Delete `_set_key()` and the `groupby`/sort logic.
- Main loop becomes:

```python
for session in pending:  # query already orders by created_at
    await svc.allocate(session)
    if session.status == SessionStatus.ACTIVE:
        started += 1
```

- Rewrite module docstring to describe global skip-FIFO.
- `kick_queue()`, the asyncio lock, and `_inflight` are unchanged.
- No database, contract, or API changes.

## Documentation changes

HANDOFF.md §4: replace the per-device-set FIFO description with global
skip-FIFO; note starvation is an accepted risk.

## Testing

TDD at implementation time, three cases:

1. Older satisfiable session starts before a newer one.
2. Unsatisfiable head is skipped; a satisfiable newer session starts in the
   same run.
3. Two sessions with the same device set: the older one wins.

## Risk

Low. One file plus docs. Behavior differs only when a queue head is
unsatisfiable: old code blocked sessions within the same set, new code never
blocks.
