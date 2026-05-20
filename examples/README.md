# OpenHarness Examples

Copy-pastable sample artifacts demonstrating each user-facing
extension surface. Drop them into the right `~/.openharness/`
subdirectory (or the project-local `.openharness/`) and run the
matching `oh ask` invocation.

The companion walkthrough is [`docs/tutorial.md`](../docs/tutorial.md);
each example below is referenced from a scenario in the tutorial.

## What's in here

```
examples/
├── README.md                       ← this file
├── commands/
│   ├── review.md                   ← simple slash command (no mode)
│   └── code-review.md              ← slash command that triggers a bundle
├── skills/
│   └── python-testing.md           ← lazy-loaded expertise (LoadSkill catalog)
├── bundles/
│   └── read-only.md                ← ModeBundle (whitelist + deny_paths + hooks)
└── hooks/
    └── turn_counter.py             ← filesystem plugin hook (Phase 5f)
```

## How to install

Two layers, both auto-discovered (per Phase 5b/5c/5d/5f
storage convention):

- **Global** (apply across all your shells):
  `~/.openharness/{commands,skills,bundles,hooks}/`
- **Project-local** (apply only inside this project):
  `<cwd>/.openharness/{commands,skills,bundles,hooks}/`

**Quick install (global)**:

```bash
mkdir -p ~/.openharness/{commands,skills,bundles,hooks}
cp examples/commands/*.md   ~/.openharness/commands/
cp examples/skills/*.md     ~/.openharness/skills/
cp examples/bundles/*.md    ~/.openharness/bundles/
cp examples/hooks/*.py      ~/.openharness/hooks/
```

**Quick install (project-local, for this repo)**:

```bash
mkdir -p .openharness/{commands,skills,bundles,hooks}
cp -r examples/* .openharness/   # then move into the right subdirs
# Or just symlink:
for d in commands skills bundles hooks; do
  ln -s "$(pwd)/examples/$d" .openharness/$d
done
```

Project-local entries override global on the same name.

## Verify each example loads

```bash
oh tools list                  # built-in tools (the framework already
                               # has these — examples don't add tools)
oh hooks list                  # shows audit_log + deny_writes builtins
oh hooks list --enable-plugin-hooks  # adds turn_counter once you've installed it
```

(There's no `oh skills list` / `oh commands list` / `oh bundles list`
in this release — those are Phase 8+ candidates. For now, verify
each artifact by running the invocation the tutorial demonstrates.)

## What each example demonstrates

| File | Surface | Phase | Tutorial section |
|---|---|---|---|
| `commands/review.md` | Slash command — pre-LLM prompt template | 5b | §2 |
| `commands/code-review.md` | Slash command with `mode:` → triggers a bundle | 5d | §3 |
| `skills/python-testing.md` | Skill — lazy-loaded expertise in the system prompt catalog | 5c | optional |
| `bundles/read-only.md` | ModeBundle — system prompt + tool whitelist + deny_paths + hooks | 5d | §3 |
| `hooks/turn_counter.py` | Filesystem plugin hook — `@hook_spec("PreApiCall")` | 5f | optional |

See the full feature index in [`README.md`](../README.md#key-features)
or the framework-level retrospectives in
[`learnings/`](../learnings/).
