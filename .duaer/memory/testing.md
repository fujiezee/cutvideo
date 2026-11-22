# Testing expectations (project contract)

Agents **must** read this file before marking work verified or accepting a job.

## Levels

| Level | What | Command (run from the repo root) | When |
|---|---|---|---|
| **L0** | Byte-compile the scripts | `python3 -m py_compile .claude/skills/video-edit/scripts/*.py` | Every implementation pass |
| **L1** | Unit tests | `python3 -m pytest .claude/skills/video-edit/tests/test_unit.py -q -p no:cacheprovider` | Any code change |
| **L2** | Pipeline tests on a synthetic 24 s clip (render, formats, loudness, auto drafts) | `python3 -m pytest .claude/skills/video-edit/tests -q -p no:cacheprovider` | Render / EDL / audio / caption changes |
| **L3** | Real-footage render of the touched feature with `--qa`, plus a frame sheet (`preview.py`) the agent looks at | job folder under `edits/` (git-ignored), e.g. `edits/tears-of-steel/` | User-visible output changes (look, sound, timing) |
| **L4** | The human watches / listens | the agent opens the output and names what to check | Voice, music, design judgement |

The full suite (L1 + L2) runs in about 45 s, offline, with no paid API.

## Risk table (what to run)

| Change touch | Minimum before accept / merge to `develop` |
|---|---|
| Docs / comments only | None (note in Brief) |
| Internal code, same output | L0 + L1 |
| Render, EDL schema, audio mix, captions, auto drafts | L0 + L2 |
| Visible / audible output | L0 + L2 + L3 (frames looked at, QA report read) |
| Paid API integration | Above, with the API mocked in tests; a live call only in L3 |

## Opt-in (still need an explicit ask or Spec note)

Mark these in the project table when they are expensive:

- Full-repo E2E (vs subset)
- Remote CI-only jobs the agent would trigger manually
- Live paid LLM / third-party smoke
- Production / staging account checks beyond local

Absence from this opt-in list means the agent **runs** the level when the risk
table requires it — the human does **not** have to re-request ordinary
verification.

## Definition of Done (agent-owned work)

Before `delivery.json` `accepted` and before merging the request branch into
local **`develop`**:

1. [ ] Required levels from the risk table have been **executed** (or waived in writing)
2. [ ] Failures fixed or the job left `open` with a clear remaining note
3. [ ] User/protocol-visible changes: E2E catalog scenario added/updated
4. [ ] Handoff mentions what was run (commands + result)

## Phase hooks (Duaer)

| Phase | Verification action |
|---|---|
| `specify` | Independent Test + acceptance scenarios; name expected levels |
| `plan` | Fill **Testing** with concrete commands / levels |
| `tasks` | **Default:** verification tasks per story (not optional) |
| `implement` | Run relevant checks as tasks complete |
| `converge` | Refuse `accepted` without verification evidence or waiver |

## Waivers

A waiver must state: which level, why it cannot run now, and the follow-up.
“Too slow” alone is not enough if a subset command exists.

## Machine accept gate

`.duaer/memory/verify.json` runs the full suite (L1 + L2) in the worktree before a
stamp is accepted.
