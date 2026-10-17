# Development Process

Project-specific development workflow. Agents read this via `{standards.dev_process}`.

## Branch Strategy

- `main` is the only long-lived branch and the only base. There is no `staging`.
- One issue → one branch `<type>/<N>-<slug>` (`feat/`, `fix/`, `chore/`, …) → one
  worktree created from fresh `origin/main`, never from a local HEAD → one PR into `main`.
- Releases are cut from `main` by Release Please (tags `roxabi-sense/vX.Y.Z`).
- `.dev/stack.yml` keeps `release.model: staging-train` because dev-core
  `/dev-checkup` rule N10 refuses `trunk` while `release-please.yml` exists.
  `/feature` and `/promote` resolve their base from `release.model` alone:
  `staging-train` → `staging`, which does not exist here. `/feature` has no usable
  base on this repo until [roxabi-plugins#834](https://github.com/Roxabi/roxabi-plugins/issues/834)
  adds an explicit `release.base`; there is no fallback to `main`.

## Workflow

<!-- Document the development flow for your team. Example:
  1. /dev #N → determines tier (S / F-lite / F-full)
  2. Frame → Spec → Plan (artifacts reviewed at each gate)
  3. Implement in worktree (git worktree add ../project-N)
  4. PR → review → fix → merge to staging
  5. /promote → staging→main PR → production deploy
-->

TODO: Document your development workflow.

## Code Ownership

<!-- Document who owns what. Examples:
  | Path | Owner | Review required? |
  |------|-------|:---:|
  | apps/web/ | Frontend team | Yes |
  | apps/api/ | Backend team | Yes |
  | packages/ui/ | Design system team | Yes |
  | .github/ | DevOps | Yes |
-->

TODO: Document code ownership.

## Release Process

<!-- Document how releases work. Examples:
  - /promote creates staging→main PR with changelog
  - Version bumps follow semver (breaking=major, feature=minor, fix=patch)
  - Tags created automatically on merge to main
-->

TODO: Document your release process.
