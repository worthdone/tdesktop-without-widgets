# Releases

Only the three manual OS release builds, their publisher and the manual Snap
build are enabled in this fork. Pushes, pull requests, schedules and release
hooks do not start upstream CI, issue bots or dependency update automation.

Dispatch `release-linux.yml`, `release-windows.yml` and `release-macos.yml` on
`dev`. Each workflow builds independently and keeps its package for 30 days.
Retry a failed OS workflow without rerunning successful builds. All three
packages must come from successful manual builds of the same source commit
using workflow definitions from the default branch.

```sh
gh workflow run release-linux.yml --ref dev
gh workflow run release-windows.yml --ref dev
gh workflow run release-macos.yml --ref dev
```

Windows dependencies and application compilation run in separate jobs, each
with its own six-hour budget. Completed dependencies transfer as a compressed
artifact retained for one day. If compilation fails, rerun only failed jobs:

```sh
gh api --method POST repos/worthdone/tdesktop-without-widgets/actions/runs/<run-id>/rerun-failed-jobs
```

To retry Windows using newer workflow fixes while keeping successful Linux
and macOS packages, pass the original full source commit:

```sh
gh workflow run release-windows.yml --ref dev -f source_sha=<full-source-sha>
```

If that run already built dependencies before a workflow fix, also pass
`-f dependencies_run=<run-id>` to reuse its dependency artifact. It must
contain dependencies for the same source commit and still be within the
one-day retention period.

The Windows build records the checked-out source SHA separately from the
workflow commit. The publisher verifies this metadata and requires every
package to have the same source SHA. Earlier builds without metadata use
their workflow commit as their source commit.

The publisher runs after each successful OS build. Once all three packages
are available, it creates a prerelease tagged
`v<app-version>-without-widgets.<source-commit-prefix>`.

For a custom tag or a stable release, dispatch `release.yml` manually with
`source_sha`, `tag` and `prerelease=false`. This reuses the successful builds;
automatic publication uses the default tag and marks it as a prerelease.

The repository's **Immutable releases** setting must stay enabled. The
publisher uploads all three packages and `SHA256SUMS` to a draft, verifies
the uploaded digests, then publishes it once. GitHub locks the published
assets and tag; corrections require a new release. An interrupted draft can
be resumed by rerunning the publisher with the same source commit and tag.

These packages are unsigned, and the macOS app is not notarized. Immutability
does not provide platform code signing or reproducible builds. The release
notes link each OS build and identify the common source commit.

Run publisher checks from the repository root with
`python3 .github/scripts/test_publish_release.py`.
