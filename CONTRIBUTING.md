# Contributing to PiWatch

Thanks for taking the time. PiWatch is a single-maintainer project, so the process is deliberately
small - but it is the same for every change, including the maintainer's own.

## How changes get in

1. Open an issue first for anything bigger than a typo or an obvious bug fix, so the direction can
   be agreed before you spend time on it. Use the templates under `.github/ISSUE_TEMPLATE/`.
2. Fork the repository (or branch, if you have write access) and make your change on a branch.
3. Open a pull request against `master`. The pull-request template asks for what changed and why.
4. `master` is protected: a PR merges only after the whole test stage of
   [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml) is green and the branch is up to
   date with `master` (enable auto-merge and it lands on its own once that is the case). Nobody
   pushes to `master` directly, not even the maintainer.

## What a pull request needs

- **Conventional Commits.** The version and the changelog are generated from the commit messages
  (`feat:` = minor release, `fix:` = patch release, `build:`/`ci:`/`docs:`/`test:` = no release).
  Squash-merge keeps the PR title as the commit message, so give the PR a Conventional Commit
  title. Commit messages are plain English without tool or AI attribution.
- **Green required checks.** `test-backend`, `test-frontend`, `test-security`, `test-ml`,
  `test-k8s-manifests`, `build` and `review / dependency-review` are required; a red one blocks
  the merge.
- **Tests for new functionality.** New features and bug fixes come with tests in `backend/tests/`
  (or `ml/tests/` for the standalone `ml/` project). A PR that adds behaviour without a test is
  asked to add one. The coverage badge in the README is regenerated from the backend coverage
  report on every release and is expected not to drop.
- **Lint.** `ruff check .` runs in `backend/` as part of `test-backend`; run it before pushing.
  `backend/pyproject.toml` configures it - the collectors and pollers intentionally catch broadly,
  so `BLE001` and `S110` are ignored there rather than worked around per call site.
- **Pinned dependencies.** `backend/requirements*.txt` and `ml/requirements*.txt` are installed
  with `--require-hashes`, so a new or bumped dependency needs its hashes in the file. `pip-audit`
  (`test-security`) and `npm audit --omit=dev` (`test-frontend`) must stay clean for what is
  actually shipped.
- **Deployment manifests.** Changes under `deploy/` are validated with `kubeconform`
  (`test-k8s-manifests`). The image tag in those manifests is written by the release pipeline -
  do not bump it by hand.

## Running things locally

```bash
# Backend, in demo mode - no cluster needed
cd backend && pip install --require-hashes -r requirements.txt -r requirements-dev.txt
PIWATCH_DEMO=1 uvicorn app.main:app --port 8000

# Frontend dev server (proxies to the backend)
cd frontend && npm install && npm run dev
```

Tests and lint, the same commands CI runs:

```bash
cd backend && python -m pytest tests/
cd backend && ruff check .
python -m pytest ml/tests/
cd frontend && npm ci && npm run build
```

See the README for the configuration environment variables and for deploying to a real cluster.

## Security issues

Please do not open a public issue for a vulnerability - use the private reporting path described
in [SECURITY.md](SECURITY.md). The [Code of Conduct](CODE_OF_CONDUCT.md) applies to every
interaction in this repository.
