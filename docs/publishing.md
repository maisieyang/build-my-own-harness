# Publishing OpenHarness

> Status: Phase 7 T3 prepared the artifact. **Real PyPI publish is
> user-gated per D25.3** — agent prepares + runs the local build +
> TestPyPI dry run; production `uv publish` requires the user to
> fire the button.

---

## What the agent prepared (already done in T3)

- `pyproject.toml` `[project]` metadata complete:
  - `version = "0.1.0"` (semver — first 0.x release per D25.4)
  - `description`, `readme = "README.md"`, `license = {text = "MIT"}`
  - `authors`, `keywords` (13 tags), `classifiers` (16 classifiers
    including `Development Status :: 4 - Beta`, `Typing :: Typed`,
    Python 3.10/3.11/3.12)
  - `urls` — Homepage, Repository, Documentation, Changelog, Issues,
    Development log
- `LICENSE` — MIT (already in repo since Phase 1 scaffolding)
- `CHANGELOG.md` — 0.1.0 release notes (Keep a Changelog format)
- `__version__` bumped from `0.0.1` → `0.1.0` in
  `src/openharness/__init__.py`
- `uv build` produced:
  - `dist/openharness-0.1.0-py3-none-any.whl` (176 KB)
  - `dist/openharness-0.1.0.tar.gz` (~910 KB)
- Fresh-venv install smoke verified:
  - `pip install <wheel>` succeeds
  - `oh --version` → `openharness 0.1.0`
  - `oh --help` lists all 5 subcommand groups (ask / chat / tools /
    config / hooks)
  - `oh tools list` / `oh tools show Read` / `oh hooks list` /
    `oh hooks describe audit_log` all work in the isolated install

---

## What YOU need to do for TestPyPI dry run

Optional but recommended before the real PyPI publish — verifies
the metadata renders correctly on a PyPI-shaped index without
burning the namespace on production PyPI.

### 1. Create a TestPyPI account + API token

- Register: https://test.pypi.org/account/register/
- Verify email
- Create an API token scoped to "Entire account" (no project exists
  yet — once `openharness` lands on TestPyPI you can rotate to a
  project-scoped token):
  https://test.pypi.org/manage/account/token/

### 2. Set the token in your shell (don't commit it)

```bash
export UV_PUBLISH_TOKEN="pypi-AgENdGVzdC5weXBpLm9yZw...<your-token>..."
```

### 3. Publish to TestPyPI

```bash
cd /path/to/build-my-own-harness

uv publish \
    --publish-url https://test.pypi.org/legacy/ \
    dist/openharness-0.1.0-py3-none-any.whl \
    dist/openharness-0.1.0.tar.gz
```

(If the build artifacts in `dist/` are stale, re-run `uv build`
first.)

### 4. Verify the install from TestPyPI

```bash
# Fresh venv outside the project to avoid editable-install confusion
python3 -m venv /tmp/oh-test-from-testpypi
source /tmp/oh-test-from-testpypi/bin/activate

# Install from TestPyPI + dependencies from real PyPI (extra-index)
pip install \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    openharness==0.1.0

oh --version                  # Expect: openharness 0.1.0
oh --help                     # Expect: 5 subcommand groups listed
oh tools list                 # Expect: 6 default tools

deactivate
rm -rf /tmp/oh-test-from-testpypi
```

### 5. Eyeball the rendered project page

Open https://test.pypi.org/project/openharness/0.1.0/ and check:

- README renders correctly (sections, code blocks, internal links)
- All classifiers display
- Project URLs show 6 entries
- License = MIT
- Author shown
- Requires-Python = ≥3.10
- Description matches what you set

If anything's off, fix `pyproject.toml`, bump `version` to `0.1.0a1`
or `0.1.0rc1` (so production PyPI stays clean for `0.1.0`), rebuild,
re-publish to TestPyPI. Iterate until the page reads well.

---

## What YOU do for the real PyPI publish (D25.3 gate)

**The agent does not run this command.** Per
[`decisions/23-phase-7-final-boundary.md`](../decisions/23-phase-7-final-boundary.md)
D25.3 — irreversible action; user fires the button only after
TestPyPI is happy.

### 1. Create a production PyPI account + API token

- Register: https://pypi.org/account/register/
- Create API token: https://pypi.org/manage/account/token/

### 2. Publish

```bash
export UV_PUBLISH_TOKEN="pypi-...<production-token>..."

uv publish \
    dist/openharness-0.1.0-py3-none-any.whl \
    dist/openharness-0.1.0.tar.gz
```

(Default upload URL is https://upload.pypi.org/legacy/ — no
`--publish-url` flag needed for production.)

### 3. Verify

```bash
# Fresh venv
python3 -m venv /tmp/oh-prod-verify
source /tmp/oh-prod-verify/bin/activate

pip install openharness==0.1.0
oh --version

deactivate
rm -rf /tmp/oh-prod-verify
```

### 4. Tag the git commit

```bash
git tag -a v0.1.0 -m "Release 0.1.0 — SPEC v1 closeout"
git push origin v0.1.0
```

### 5. Create a GitHub release

- Go to https://github.com/maisieyang/build-my-own-harness/releases/new
- Pick the `v0.1.0` tag
- Title: `v0.1.0 — SPEC v1 closeout`
- Body: copy the 0.1.0 entry from `CHANGELOG.md`
- Optionally attach `dist/openharness-0.1.0-py3-none-any.whl` +
  `dist/openharness-0.1.0.tar.gz` as release assets

---

## If something goes wrong

### "File already exists" on TestPyPI / PyPI

You can't overwrite an existing version. Bump to `0.1.1` (or
`0.1.0.post1` for non-code metadata fixes) in `pyproject.toml` +
`src/openharness/__init__.py`, rebuild, retry.

### TestPyPI 503 / timeout

TestPyPI is hosted on smaller infra than production. Retry; if
persistent, skip TestPyPI and go to production directly (riskier
but fine for a 0.1.0 first release after thorough local smoke).

### Metadata render issues on PyPI

Common causes:
- README has relative links that don't resolve on PyPI's renderer
  (PyPI serves Markdown but doesn't resolve `./decisions/...`
  paths). Either accept the broken links on PyPI or absolutize them
  to GitHub URLs.
- Classifier typo (PyPI rejects unknown classifiers at upload time;
  catches at TestPyPI dry run).

### Forgot to bump version

`pip` won't install a newer version that doesn't exist. `pip
install --upgrade openharness` will say "already up to date" if
you forgot the bump.

---

## Trusted publishing (future)

For automated releases via GitHub Actions, configure PyPI's
[trusted publishing](https://docs.pypi.org/trusted-publishers/) +
add a `.github/workflows/release.yml`. Not done for 0.1.0 —
manual local publish is fine for the first release while the API
is still pre-1.0.
