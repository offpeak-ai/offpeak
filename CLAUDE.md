# CLAUDE.md

Operational rules for this repository. Public repo — nothing strategic here.

## Commits

- Author every commit as `theo-titonis <315989724+theo-titonis@users.noreply.github.com>`.
- No co-author trailers of any kind.
- An authors-guard CI job enforces this.

## Branch protection

`main` is protected: branch → PR → 4 green checks. Tests are network-free. `ruff` must be clean. CI runs on Python 3.10–3.13.

## Releases

- Bump the version, tag `vX.Y.Z`, then create the GitHub Release from that tag — never `--target`.
- Trusted Publishing ships to PyPI.
- Same-day rule: any `src` change on `main` ships a release the same day; `main` then bumps to the next `.dev0`.

## board-data

Written only by workflows, as `github-actions[bot]`. Never commit to it by hand.

## Public API

`job()` / `run()` / `quote()` / `receipt()`. There is no public `submit()`.

## Copy

"nightly" is fine as a cadence word. Never use "night" as a value frame.

## PR bodies

Describe what changed. Never why. Keep them operational.

## Scheduling

GitHub's scheduler delivers no cron events to this repo; workflows are dispatched externally. Do not add or rely on `schedule:` triggers.
