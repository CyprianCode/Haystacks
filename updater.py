"""
Update checks against the project's GitHub releases, and one-click updating.

Once a day the app asks GitHub for the latest release. If it is newer than
version.py, the main window shows a banner. "Update now" downloads the
release's Haystacks-Setup-<version>.exe, checks its SHA-256 (GitHub's asset
digest, or a .sha256 file next to it), runs it quietly and closes the app;
the installer starts Haystacks again when it is done.

HAYSTACKS_UPDATE_URL points the check somewhere else for testing (any URL
urllib can open, including file:///...), and forces a check on every start.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from version import __version__

REPO = "CyprianCode/Haystacks"
TEST_URL = os.environ.get("HAYSTACKS_UPDATE_URL")
API_URL = TEST_URL or f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY_S = 24 * 3600
ASSET = re.compile(r"^Haystacks-Setup-[\w.\-]+\.exe$", re.IGNORECASE)


def parse_version(text):
    """'v1.2.3' -> (1, 2, 3)."""
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Haystacks/{__version__}"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest_release():
    """The newest published release as a dict, or None if there is none
    or it has no installer."""
    with _get(API_URL) as resp:
        data = json.load(resp)
    if data.get("draft") or data.get("prerelease"):
        return None
    assets = data.get("assets", [])
    setup = next((a for a in assets if ASSET.match(a.get("name", ""))), None)
    if not setup:
        return None
    sha = None
    digest = setup.get("digest") or ""
    if digest.startswith("sha256:"):
        sha = digest.split(":", 1)[1].lower()
    checksum_file = next((a for a in assets if a.get("name") == setup["name"] + ".sha256"), None)
    tag = data.get("tag_name", "")
    return {"tag": tag, "version": ".".join(map(str, parse_version(tag))),
            "page": data.get("html_url") or f"https://github.com/{REPO}/releases",
            "name": setup["name"], "url": setup["browser_download_url"],
            "size": setup.get("size", 0), "sha256": sha,
            "sha256_url": checksum_file["browser_download_url"] if checksum_file else None}


def is_newer(release):
    return parse_version(release["tag"]) > parse_version(__version__)


def check(settings):
    """Run in a thread. Returns a newer release to offer, or None. Records the
    check time in settings (the caller saves them)."""
    update = settings.setdefault("update", {})
    if not TEST_URL and time.time() - update.get("last_check", 0) < CHECK_EVERY_S:
        return None
    release = latest_release()
    update["last_check"] = time.time()
    if release and is_newer(release) and update.get("skipped") != release["tag"]:
        return release
    return None


def expected_sha256(release):
    if release["sha256"]:
        return release["sha256"]
    if release["sha256_url"]:
        with _get(release["sha256_url"]) as resp:
            return resp.read().decode().split()[0].lower()
    return None


def download(release, on_progress=None, cancel=None):
    """Download and verify the installer. Returns its path. Refuses an
    installer it can't verify."""
    expected = expected_sha256(release)
    if not expected:
        raise RuntimeError("this release has no checksum, so it can't be verified")
    return fetch_file(release["url"], Path(tempfile.gettempdir()) / release["name"],
                      expected, release["size"], on_progress, cancel)


def fetch_file(url, target: Path, sha256=None, size=0, on_progress=None, cancel=None):
    """Download url to target through a .part file. With sha256, a file that
    doesn't match is deleted and refused. Returns target."""
    target = Path(target)
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    got = 0
    with _get(url, timeout=60) as resp, open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or size or 0)
        while True:
            if cancel is not None and cancel.is_set():
                f.close()
                part.unlink(missing_ok=True)
                raise InterruptedError("stopped")
            block = resp.read(1 << 20)
            if not block:
                break
            f.write(block)
            digest.update(block)
            got += len(block)
            if on_progress:
                on_progress(got, total)
    if sha256 and digest.hexdigest() != sha256.lower():
        part.unlink(missing_ok=True)
        raise RuntimeError("the download is damaged (checksum mismatch); try again later")
    part.replace(target)
    return target


def run_installer(installer: Path):
    """Start the installer quietly, detached, so it can replace this app once
    it has closed. Its last step starts Haystacks again."""
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                      "/CLOSEAPPLICATIONS"], creationflags=flags, close_fds=True)
