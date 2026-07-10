# Releasing django-icv-tree

How to cut a release for this package.

## TL;DR

```
branch  ->  PR  ->  review  ->  merge to main  ->  tag the merged commit  ->  CI publishes
```

The **tag is the trigger**. Pushing a tag of the form `v<semver>` runs the
publish workflow, which tests, builds, uploads to PyPI (OIDC trusted publishing,
no token), and creates a GitHub release. **Tagging is publishing. There is no
separate "publish" step and no undo.**

## Canonical flow

1. **Branch** off `main`:
   `release/<version>` (e.g. `release/0.3.0`).
2. **Bump** on the branch:
   - `pyproject.toml` under `[project]` version
   - `src/icv_tree/__init__.py` the `__version__` string (keep in sync)
   - `CHANGELOG.md`: rename `[Unreleased]` to `[<version>] - <YYYY-MM-DD>`
3. **Open a PR** to `main`. CI runs lint and tests. Get it reviewed. This is the
   gate: do not skip it.
4. **Merge to `main`** (squash or merge, per repo norm).
5. **Tag the merged commit on `main`** and push the tag:
   ```bash
   git checkout main && git pull
   git tag v<version>
   git push origin v<version>
   ```
6. **Watch the publish run** and confirm PyPI:
   ```bash
   gh run watch <run-id> --exit-status
   curl -s https://pypi.org/pypi/django-icv-tree/json | python -c \
     "import sys,json;print(json.load(sys.stdin)['info']['version'])"
   ```

Tag the commit that is on `main`, not a feature branch. Tags point at commits,
not branches, so tagging a feature branch will publish, but it publishes code
that may never have been merged. Always tag after the merge.

## Tag format (strict)

`v<semver>`: just `v` then the version number. The publish workflow matches
`v*` and parses the version by stripping the leading `v`.

Examples: `v0.2.1`, `v0.3.0`, `v1.0.0`.

## Versioning (SemVer)

[Semantic Versioning](https://semver.org/). Pre-1.0, the rules still apply with
the usual pre-1.0 caveat that minor bumps may carry breaking changes:

- **Patch** (`0.3.1 -> 0.3.2`): bug fixes, doc-only changes, no API or behaviour
  change.
- **Minor** (`0.3.1 -> 0.4.0`): new public API, **any behaviour change** (even a
  safer one), or a raised minimum dependency floor (e.g. Django).
- **Major** (`0.x -> 1.0`): the stability commitment; breaking changes after 1.0.

If in doubt between patch and minor, choose minor. Burning a version number is
free; shipping a behaviour change as a patch surprises consumers.

## Downstream impact: django-icv-taxonomy

`django-icv-taxonomy` (in its own standalone repo) depends on this package.
Any breaking change to `icv_tree`'s public API requires a coordinated release of
`django-icv-taxonomy` that pins or adapts to the new version. Flag breaking
changes in the CHANGELOG and open a companion issue in the taxonomy repo before
tagging.

## CHANGELOG (required for every release)

**A release MUST include a CHANGELOG entry for its version. No entry, no tag.**
The publish workflow's `resolve` job greps `CHANGELOG.md` for the version heading
and exits with an error if it is absent.

Format: [Keep a Changelog](https://keepachangelog.com/). Accumulate entries under
`## [Unreleased]` as you work; at release time rename that heading to
`## [<version>] - <YYYY-MM-DD>`. Subsections: Added / Changed / Fixed / Removed.

Call out behaviour changes explicitly, including ones that are "safer": a
consumer relying on the old behaviour still needs to know.

## Django pin

The publish workflow's test job pins `Django~=5.1.0`. When you raise the minimum
Django in `pyproject.toml`, update that pin in the same PR, or the tagged build's
test job can fail to resolve dependencies and block the publish.

## Pre-tag checklist

Before pushing the tag (the irreversible step):

- [ ] `CHANGELOG.md` has a `[<version>] - <date>` entry (renamed from `[Unreleased]`).
- [ ] Behaviour changes and breaking changes called out in that CHANGELOG entry.
- [ ] Version bumped in `pyproject.toml` **and** `src/icv_tree/__init__.py`, and they match.
- [ ] Django pin in `.github/workflows/publish.yml` matches the package's minimum, if the floor changed.
- [ ] Tests pass locally and the package builds (`python -m build` at repo root).
- [ ] The PR is **merged to `main`** and you are tagging that commit.
- [ ] Tag format is `v<version>`.
- [ ] This exact version has never been published (PyPI rejects re-uploads).
- [ ] If the release contains breaking changes, a companion issue is open in the taxonomy repo.

## If something goes wrong

- **PyPI rejects the upload (version exists).** That version is permanently taken.
  Bump to the next patch and re-tag.
- **The test or build job fails after tagging.** Nothing was published (publish
  is the last job and depends on test and build). Fix on a new PR, merge, delete
  the bad tag (`git push --delete origin <tag>`), and re-tag the new commit with
  the **same** version (since nothing reached PyPI).
- **Published, but the code is not on `main`.** Open a PR from the release branch
  to `main` immediately and merge, so `main` reflects what is on PyPI. Avoid this
  by always tagging after the merge.

## Optional hardening

Consider adding a **manual approval gate** to the `publish` job via a protected
GitHub Environment (`pypi`), so "push tag" and "irreversibly upload to PyPI" are
decoupled: a human approves the upload after seeing test and build go green. The
workflow already declares `environment: pypi`; add a required-reviewer protection
rule to that environment to enable the gate.
