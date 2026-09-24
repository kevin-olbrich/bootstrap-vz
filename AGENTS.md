# bootstrap-vz
Unattended, manifest-driven bootstrapper that builds ready-to-boot Debian images
for EC2, GCE, Azure, Oracle, KVM, VirtualBox and Docker. This repository is the
maintained fork of the no longer maintained upstream `andsens/bootstrap-vz`.

## Working style
These rules favor caution over speed. For trivial tasks, use judgment.
- Think before coding. State your assumptions. When a request has several
  readings, present them instead of picking one silently. If something is
  unclear, stop and ask. Point out a simpler approach when one exists.
- Keep it simple. Write the minimum code that solves the problem, with no
  unrequested features, configurability or single-use abstractions, and no
  error handling for impossible cases. If 200 lines could be 50, rewrite them.
- Make surgical changes. Every changed line must trace back to the request.
  Do not reformat, refactor or "improve" adjacent code, and match the existing
  style. Remove imports and functions that your own change orphaned. Mention
  unrelated dead code instead of deleting it.
- Work toward verifiable goals. Turn a task into a check, such as a failing
  test that reproduces the bug and then passes, or tests that pass before and
  after a refactor. For multi-step work, state a short plan with a
  verification for each step, and loop until it passes. See "Known state" below
  for which checks can currently run.

## Stack
- Python 3.13 only (`python_requires='>=3.13'` in `setup.py`, `basepython` in `tox.ini`).
  Use `python3` in shebangs and invocations, never bare `python`.
- Packaging: `setup.py` (setuptools). Version lives in `bootstrapvz/__init__.py`.
- Main libraries: `fysom` (task state machine), `jsonschema` + `pyyaml` (manifest
  validation), `boto3` (EC2), `pyro4` (remote bootstrapping).
- All checks run through `tox`. CI config is `.travis.yml`, which just runs `tox`.
- Docs: Sphinx (`docs/`), published on Read the Docs.

## Commands
Run these from the repository root. They need a `python3.13` interpreter and `tox`.

```sh
python3.13 -m pip install tox                    # setup
tox -e flake8                                    # style check (max line length 110)
tox -e pylint                                    # static analysis (pylintrc)
tox -e yamllint                                  # lint manifests/ (max line length 160)
tox -e unit                                      # unit tests (tests/unit)
tox -e unit -- tests/unit/releases_tests.py::test_lt  # a single test (pytest)
tox -e integration                               # dry-run every manifest in manifests/
tox -e docs                                      # Sphinx build with -W (warnings are errors)
./bootstrap-vz --dry-run manifests/examples/kvm/buster-cloudimg.yml  # no root, no side effects
sudo ./bootstrap-vz <manifest>                   # real build (root required)
```

`tox -e system` runs the system tests. They build and boot real images, which
costs cloud money, so run them only when the user explicitly asks.

Known state as of the Python 3.13 migration. Fix these issues or work around
them, and never paper over them:
- `flake8` reports existing F401/E741 findings in `common/tasks/apt.py` and in
  `plugins/minimize_size/tasks/`, and `pylint` exits non-zero.
- `docs` fails only on a YAML highlighting warning: the example in
  `manifests/README.rst` uses an unquoted `*`, which is invalid YAML.
- `yamllint`, `unit` and `integration` pass. Do not add new findings to any env.

## Structure
- `bootstrapvz/base/`: the core engine. It loads the manifest, validates it against
  `manifest-schema.yml`, resolves the tasklist and runs tasks ordered by phase and
  predecessors/successors.
- `bootstrapvz/common/`: shared tasks (`tasks/`), `phases.py` (the fixed phase order),
  `releases.py` (Debian release names and aliases such as `stable`), `task_groups.py`.
- `bootstrapvz/providers/<name>/` and `bootstrapvz/plugins/<name>/`: each one has
  an `__init__.py` with `validate_manifest(data, validator, error)` and
  `resolve_tasks(taskset, manifest)`, plus a `manifest-schema.yml`, a `README.rst`,
  tasks, and optional `assets/`.
- `bootstrapvz/remote/`: build servers and a Pyro4 bridge for remote builds
  (`bootstrap-vz-remote`, `bootstrap-vz-server`).
- `manifests/official/` and `manifests/examples/`: every manifest here is dry-run by
  the integration tests, so a manifest must stay valid after a schema change.
- `docs/`: Sphinx sources. `docs/conf.py` generates the provider and plugin pages
  from their `README.rst`, and `docs/transform_github_links.py` rewrites links.

## Conventions
These rules come from `CONTRIBUTING.rst`, which has the full reasoning.
- The manifest must fully describe the resulting image. Never make the outcome
  depend on host state or settings outside the manifest, because builds must be
  reproducible and shareable.
- Builds must run fully unattended. Never add prompts.
- Keep tasks stateless. A task is a class with a `description`, a `phase`,
  optional `predecessors`/`successors`, and a `@classmethod run(cls, info)`.
  Shared state goes on the `BootstrapInformation` object (`info`), and only when
  it is really necessary.
- Decide whether a task applies in `resolve_tasks()`, not inside `run()`. That way
  the logged tasklist shows exactly what work was done.
- Split complex `run()` bodies into separate tasks so other plugins can replace
  or interleave steps (for example, `prebootstrapped` swaps out volume creation).
- Validate manifest settings with the JSON schema (`manifest-schema.yml`). Use
  Python checks only when a schema check would be much more complex.
- When calling external programs through `log_check_call`/`log_call`, use long
  options (`--quiet`, not `-q`) and rely on `$PATH` (`wget`, not `/usr/bin/wget`).
- Compare Debian releases with the objects in `bootstrapvz/common/releases.py`,
  never with string comparisons. When Debian releases a new version, update the
  aliases there.
- Code style is PEP8 with a 110-character line limit. Vertically aligned `=` in
  assignment blocks is allowed (E221/E241 are ignored).
- When you add or change a provider or plugin, update its `README.rst`. Link
  other rst files with relative links that work on GitHub, and point at folder
  names instead of `README.rst`.
- Work directly on `master`. Do not create feature branches unless asked.
- Record significant changes (new plugin, new manifest setting, security fix)
  in `CHANGELOG.rst`.
- Commit messages: write a short imperative subject line, for example "Wait for
  nbd device to be ready before partitioning". Follow it with a blank line and
  a body that explains what changed and why. Never reference an AI tool or
  agent in a commit: no co-author trailers (for example, never write
  "Co-Authored-By: Claude Fable 5") and no "generated with" lines.

## Boundaries
- Never commit build or test output: `build/`, `dist/`, `*.egg-info/`, `.tox/`,
  `docs/_build/`, `.coverage`, `build-servers.yml` or `integration.html`.
- `build-servers.yml` holds local credentials for remote builds. Never create it
  with real values or read secrets out of it.
- Do not edit `LICENSE` or remove the original author's attribution. The fork
  keeps the upstream copyright under Apache 2.0.
- Do not run real image builds, system tests or anything that calls cloud
  provider APIs unless the user explicitly asks. These need root and loop/nbd
  devices, and they can create billable resources.
- Tool-specific instruction files (`CLAUDE.md`, `GEMINI.md`, `.cursorrules`,
  `.clinerules`, `.windsurfrules`, `.github/copilot-instructions.md`) are pointers
  only. Put project rules here in `AGENTS.md`.
