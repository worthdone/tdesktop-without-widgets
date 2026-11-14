# Releases

Dispatch `release-linux.yml`, `release-windows.yml` and `release-macos.yml` on
`dev`. Each workflow builds independently and keeps its package for 30 days.
Retry a failed OS workflow without rerunning successful builds. All three
packages must come from successful manual builds of the same source commit
on the default branch.

```sh
gh workflow run release-linux.yml --ref dev
gh workflow run release-windows.yml --ref dev
gh workflow run release-macos.yml --ref dev
```

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
