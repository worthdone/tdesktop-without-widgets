import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


PACKAGES = {
    "linux": "tdesktop-without-widgets-linux-x64.tar.xz",
    "windows": "tdesktop-without-widgets-windows-x64.zip",
    "macos": "tdesktop-without-widgets-macos-universal.zip",
}


def gh(*args):
    return subprocess.check_output(["gh", *args], text=True)


def api(path):
    return json.loads(gh("api", path))


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pages(path, key=None):
    result = []
    for page in range(1, 1000):
        separator = "&" if "?" in path else "?"
        response = api(f"{path}{separator}per_page=100&page={page}")
        batch = response[key] if key else response
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise RuntimeError("API pagination limit reached")


def trusted_run(run, repo, branch, sha, platform):
    return (
        run["head_sha"] == sha
        and run["head_branch"] == branch
        and run["head_repository"]["full_name"] == repo
        and run["event"] == "workflow_dispatch"
        and run["status"] == "completed"
        and run["conclusion"] == "success"
        and run["path"] == f".github/workflows/release-{platform}.yml"
    )


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    root = f"repos/{repo}"
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    automatic = os.environ["GITHUB_EVENT_NAME"] == "workflow_run"
    branch = event["repository"]["default_branch"]
    source = event["workflow_run"]["head_sha"] if automatic else os.environ.get("SOURCE_SHA", "")
    sha = source or api(f"{root}/commits/{branch}")["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("A full source commit SHA is required")

    builds = {}
    for platform in PACKAGES:
        path = f"{root}/actions/workflows/release-{platform}.yml/runs?head_sha={sha}&event=workflow_dispatch"
        candidates = [run for run in pages(path, "workflow_runs")
                      if trusted_run(run, repo, branch, sha, platform)]
        if not candidates:
            message = f"Waiting for a successful {platform} build of {sha}."
            if automatic:
                print(message)
                return
            raise RuntimeError(message)
        builds[platform] = max(candidates, key=lambda run: run["id"])

    version_file = api(f"{root}/contents/Telegram/build/version?ref={sha}")
    version_text = base64.b64decode(version_file["content"]).decode()
    version = re.search(r"^AppVersionStr\s+([0-9.]+)$", version_text, re.MULTILINE).group(1)
    tag = os.environ.get("RELEASE_TAG") or f"v{version}-without-widgets.{sha[:12]}"
    if tag.startswith("-") or "\n" in tag or "\r" in tag:
        raise ValueError("Invalid release tag")
    subprocess.run(["git", "check-ref-format", f"refs/tags/{tag}"], check=True)
    releases = pages(f"{root}/releases")
    release = next((item for item in releases if item["tag_name"] == tag), None)
    if release and not release["draft"]:
        if automatic and release["target_commitish"] == sha and release.get("immutable"):
            print(f"Already published: {release['html_url']}")
            return
        raise RuntimeError(f"Release {tag} is already published")
    if release and release["target_commitish"] != sha:
        raise RuntimeError("Existing draft targets a different source commit")
    refs = pages(f"{root}/git/matching-refs/tags/")
    for item in refs:
        if item["ref"] == f"refs/tags/{tag}":
            if not release or item["object"]["type"] != "commit" or item["object"]["sha"] != sha:
                raise RuntimeError("Release tag already exists; choose a new tag")

    with tempfile.TemporaryDirectory() as directory:
        dist = Path(directory)
        checksums = []
        for platform, filename in PACKAGES.items():
            run_id = builds[platform]["id"]
            artifacts = pages(f"{root}/actions/runs/{run_id}/artifacts", "artifacts")
            matching = [item for item in artifacts if item["name"] == f"release-{platform}"
                        and not item["expired"]]
            if len(matching) != 1:
                raise RuntimeError(f"Missing or ambiguous {platform} artifact")
            destination = dist / platform
            gh("run", "download", str(run_id), "--repo", repo,
               "--name", f"release-{platform}", "--dir", str(destination))
            package = destination / filename
            if list(destination.iterdir()) != [package] or not package.is_file() or not package.stat().st_size:
                raise RuntimeError(f"Unexpected {platform} artifact contents")
            package.rename(dist / filename)
            destination.rmdir()
            digest = file_digest(dist / filename)
            checksums.append(f"{digest}  {filename}\n")
        (dist / "SHA256SUMS").write_text("".join(checksums))
        notes = dist / "notes.txt"
        notes.write_text(
            "Community builds of Telegram Desktop without widgets.\n\n"
            "Windows x64, macOS universal (Intel and Apple Silicon), and Linux x64.\n"
            "These builds are unsigned and macOS builds are not notarized.\n"
            "Automatic updates and crash reporting are disabled.\n\n"
            f"Source commit: {sha}\n\n"
            + "\n".join(f"{platform}: {run['html_url']}" for platform, run in builds.items())
            + "\n"
        )
        if not release:
            args = ["release", "create", tag, "--repo", repo, "--draft", "--target", sha,
                    "--title", f"Telegram Desktop without widgets {tag}", "--notes-file", str(notes)]
            if os.environ.get("PRERELEASE") == "true":
                args.append("--prerelease")
            gh(*args)
            release = next(item for item in pages(f"{root}/releases") if item["tag_name"] == tag)
        expected = [*PACKAGES.values(), "SHA256SUMS"]
        gh("release", "upload", tag, "--repo", repo, "--clobber",
           *(str(dist / filename) for filename in expected))
        assets = api(f"{root}/releases/{release['id']}/assets")
        if sorted(item["name"] for item in assets) != sorted(expected):
            raise RuntimeError("Draft does not contain exactly the expected assets")
        for asset in assets:
            digest = "sha256:" + file_digest(dist / asset["name"])
            if asset["size"] != (dist / asset["name"]).stat().st_size or asset.get("digest") != digest:
                raise RuntimeError(f"Uploaded asset verification failed: {asset['name']}")
        published = json.loads(gh("api", "--method", "PATCH", f"{root}/releases/{release['id']}",
                                  "-F", "draft=false"))
        if not published.get("immutable"):
            raise RuntimeError("Release was published but GitHub did not mark it immutable")
        print(published["html_url"])
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            summary.write(f"Published immutable release: {published['html_url']}\n")


if __name__ == "__main__":
    main()
