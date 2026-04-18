# Architecture Decision Records

Decisions that shape the system live here. One file per decision.

## File naming

`NNNN-short-title.md`, zero-padded to four digits. Example: `0001-use-postgres-rls-for-tenancy.md`.

Numbers are sequential and never reused. If a decision is superseded, write a new ADR that references the old one.

## Template

```markdown
# NNNN. Title

- Status: Proposed | Accepted | Superseded by NNNN | Deprecated
- Date: YYYY-MM-DD

## Context

What problem are we solving. What forces are in play. What constraints apply.

## Decision

What we decided. One clear statement.

## Consequences

Positive, negative, and neutral outcomes. What becomes easier. What becomes harder. What we now have to maintain.

## Alternatives considered

Brief notes on paths not taken and why.
```

## Scope

ADRs are for decisions that are hard to reverse: data model, multi-tenancy model, retrieval pipeline shape, agent framework, primary databases. Not for small code choices.
