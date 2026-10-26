#!/usr/bin/env python3
"""Read data only from the exact successful, trusted image run; never extract code."""
import io
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import zipfile

REPO = "kyth-os/Rowyn"
IMAGE = "ghcr.io/kyth-os/rowyn"


def api(path):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + path,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def trusted_run(run):
    require(run["repository"]["full_name"] == REPO and
            run["head_repository"]["full_name"] == REPO and
            run["head_branch"] == "main" and
            run["event"] in ("push", "workflow_dispatch") and
            run["path"] == ".github/workflows/build.yml" and
            run["status"] == "completed" and run["conclusion"] == "success",
            "not a successful trusted main image release")


def resolve(event_name, event, repository, ref, fetch=api, download=None):
    require(repository == REPO, "unexpected repository")
    if event_name == "workflow_dispatch":
        require(ref == "refs/heads/main", "manual ISO releases require main")
        digest = event["inputs"]["image_digest"]
        sha = event["inputs"]["source_sha"]
    else:
        require(event_name == "workflow_run", "ISO build requires upstream run or explicit digest")
        upstream = event["workflow_run"]
        trusted_run(upstream)
        run_id = upstream["id"]
        require(type(run_id) is int and run_id > 0, "invalid run ID")
        run = fetch(f"/actions/runs/{run_id}")
        trusted_run(run)
        require(run["id"] == run_id and run["head_sha"] == upstream["head_sha"] and
                run["run_attempt"] == upstream["run_attempt"], "upstream run changed")
        sha = run["head_sha"]
        artifacts = fetch(f"/actions/runs/{run_id}/artifacts?per_page=100")
        require(artifacts["total_count"] == len(artifacts["artifacts"]), "incomplete artifact listing")
        matches = [a for a in artifacts["artifacts"] if a["name"] == "rowyn-image-metadata-" + sha]
        require(len(matches) == 1 and not matches[0]["expired"], "missing or ambiguous image metadata")
        artifact = matches[0]
        require(artifact["size_in_bytes"] <= 16384, "oversized image metadata")
        # The archive endpoint is constructed from the trusted repository and artifact ID,
        # not a URL supplied inside an artifact. See the no-auth redirect below.
        data = download(artifact["id"]) if download else download_artifact(artifact["id"])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            require(archive.namelist() == ["image-build-metadata.json"], "unexpected metadata archive entries")
            require(archive.infolist()[0].file_size <= 16384, "oversized metadata entry")
            metadata = json.loads(archive.read("image-build-metadata.json"))
        require(metadata["run_id"] == str(run_id) and metadata["source_sha"] == sha and
                metadata["image"] == IMAGE + ":" + sha, "metadata does not match upstream run")
        digest = metadata["digest"]
    require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha), "invalid source SHA")
    require(isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "invalid image digest")
    return {"image_ref": IMAGE + ":" + sha, "image_digest": digest,
            "source_sha": sha, "source_image": IMAGE + "@" + digest}


def download_artifact(artifact_id):
    require(type(artifact_id) is int and artifact_id > 0, "invalid artifact ID")
    # Fetch redirect without forwarding a GitHub bearer token to blob storage.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"]},
    )
    try:
        urllib.request.build_opener(NoRedirect).open(request, timeout=60)
    except urllib.error.HTTPError as error:
        require(error.code == 302, "artifact download failed")
        location = error.headers["Location"]
        error.close()
        require(location.startswith("https://"), "unsafe artifact redirect")
    else:
        raise ValueError("expected artifact redirect")
    with urllib.request.urlopen(location, timeout=60) as response:
        data = response.read(16385)
    require(len(data) <= 16384, "oversized archive")
    return data


if __name__ == "__main__":
    result = resolve(os.environ["GITHUB_EVENT_NAME"],
                     json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
                     os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REF"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")
