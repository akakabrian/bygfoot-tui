# DOGFOOD — bygfoot-tui

_Session: 2026-04-23T12:15:37, driver: pty, duration: 8.0 min_

**PASS** — ran for 4.6m, captured 63 snap(s), 1 milestone(s), 0 blocker(s), 0 major(s).

## Summary

Ran a rule-based exploratory session via `pty` driver. Found 1 UX note(s). Game reached 14 unique state snapshots. Captured 1 milestone shot(s); top candidates promoted to `screenshots/candidates/`. 1 coverage note(s) — see Coverage section.

## Findings

### Blockers

_None._

### Majors

_None._

### Minors

_None._

### Nits

_None._

### UX (feel-better-ifs)
- **[U1] state() feedback is coarse**
  - Only 14 unique states over 306 samples (ratio 0.05). The driver interface works but reveals little per tick.

## Coverage

- Driver backend: `pty`
- Keys pressed: 1882 (unique: 21)
- State samples: 306 (unique: 14)
- Score samples: 0
- Milestones captured: 1
- Phase durations (s): A=216.5, B=8.8, C=48.1
- Snapshots: `/home/brian/AI/projects/tui-dogfood/reports/snaps/bygfoot-tui-20260423-121032`

Unique keys exercised: /, 3, :, ?, H, R, c, down, enter, escape, h, left, n, p, question_mark, r, right, shift+slash, space, up, z

### Coverage notes

- **[CN1] Phase B exited early due to saturation**
  - State hash unchanged for 10 consecutive samples during the stress probe; remaining keys skipped.

## Milestones

| Event | t (s) | Interest | File | Note |
|---|---|---|---|---|
| first_input | 0.3 | 0.0 | `bygfoot-tui-20260423-121032/milestones/first_input.txt` | key=right |
