from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from uuid import uuid4

from core.runtime_environment import ReleaseVerificationError

logger = logging.getLogger(__name__)

DEFAULT_MYAGENT_HOME = Path.home() / ".my-agent"


def normalize_link_path(path: Path) -> Path:
    """Normalizes a symlink path without dereferencing the symlink itself."""
    expanded = path.expanduser()
    return expanded.parent.resolve() / expanded.name


def _compute_file_sha256(filepath: Path) -> str:
    """Computes SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    commit_sha: str
    short_sha: str
    source_ref: str
    installed_at: str
    release_path: Path
    is_active_stable: bool
    tree_hash: str | None = None
    venv_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_sha": self.commit_sha,
            "short_sha": self.short_sha,
            "source_ref": self.source_ref,
            "installed_at": self.installed_at,
            "release_path": str(self.release_path),
            "is_active_stable": self.is_active_stable,
            "tree_hash": self.tree_hash,
            "venv_path": str(self.venv_path) if self.venv_path else None,
        }


class ReleaseManager:
    """Manages version-pinned, reproducible local Stable releases for MyAgent."""

    def __init__(
        self,
        releases_root: Path | None = None,
        stable_link: Path | None = None,
        venvs_root: Path | None = None,
        data_dir: Path | None = None,
    ) -> None:
        base_home = Path(os.getenv("MYAGENT_HOME", str(DEFAULT_MYAGENT_HOME))).expanduser().resolve()
        self.releases_root = (
            releases_root
            or Path(os.getenv("MYAGENT_RELEASES_ROOT", str(base_home / "releases")))
        ).expanduser().resolve()
        raw_link = (
            stable_link
            or Path(os.getenv("MYAGENT_STABLE_LINK", str(base_home / "stable")))
        )
        self.stable_link = normalize_link_path(raw_link)
        self.venvs_root = (
            venvs_root
            or Path(os.getenv("MYAGENT_VENVS_ROOT", str(base_home / "venvs")))
        ).expanduser().resolve()
        self.data_dir = (
            data_dir
            or Path(os.getenv("MYAGENT_DATA_DIR", str(base_home / "data")))
        ).expanduser().resolve()

        # Ensure base directories exist
        self.releases_root.mkdir(parents=True, exist_ok=True)
        self.venvs_root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolve_git_commit(
        self,
        git_ref: str,
        repo_root: Path,
        require_main_ancestry: bool = True,
    ) -> tuple[str, str, str]:
        """Resolves git_ref into (full_sha, short_sha, tree_hash) via git rev-parse."""
        try:
            full_sha = subprocess.check_output(
                ["git", "rev-parse", "--verify", f"{git_ref}^{{commit}}"],
                cwd=str(repo_root),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

            short_sha = subprocess.check_output(
                ["git", "rev-parse", "--short", full_sha],
                cwd=str(repo_root),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

            tree_hash = subprocess.check_output(
                ["git", "rev-parse", f"{full_sha}^{{tree}}"],
                cwd=str(repo_root),
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

            if require_main_ancestry and (repo_root / ".git").exists():
                is_ancestor = False
                for branch in ("main", "origin/main"):
                    try:
                        subprocess.check_call(
                            ["git", "merge-base", "--is-ancestor", full_sha, branch],
                            cwd=str(repo_root),
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                        )
                        is_ancestor = True
                        break
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        continue

                if not is_ancestor:
                    raise ReleaseVerificationError(
                        f"Commit '{full_sha}' is not an approved ancestor of 'main'. "
                        "Unmerged feature branches cannot be staged or installed as Stable releases."
                    )

            return full_sha, short_sha, tree_hash
        except subprocess.CalledProcessError as exc:
            raise ReleaseVerificationError(
                f"Failed to resolve git ref '{git_ref}' in repo '{repo_root}': {exc}"
            ) from exc

    def stage_release(
        self,
        git_ref: str,
        repo_root: Path,
        *,
        create_venv: bool = False,
        python_executable: str | None = None,
    ) -> ReleaseRecord:
        """Stages an immutable release archive directly from a verified git commit without activating it."""
        return self.install_release(
            git_ref=git_ref,
            repo_root=repo_root,
            create_venv=create_venv,
            python_executable=python_executable,
            activate_as_stable=False,
        )

    def install_release(
        self,
        git_ref: str,
        repo_root: Path,
        *,
        create_venv: bool = False,
        python_executable: str | None = None,
        activate_as_stable: bool = False,
    ) -> ReleaseRecord:
        """Installs an immutable release archive directly from a verified git commit.

        INVARIANT: Installing or staging a release does NOT activate it by default.
        Activation requires an explicit promote_release call or activate_as_stable=True.
        """
        repo_root = repo_root.expanduser().resolve()
        full_sha, short_sha, tree_hash = self.resolve_git_commit(git_ref, repo_root)

        target_release_dir = self.releases_root / full_sha
        tmp_build_dir = self.releases_root / f".tmp_{full_sha}_{uuid4().hex[:8]}"

        if target_release_dir.exists():
            logger.info("Release '%s' already exists at '%s'. Skipping export.", full_sha, target_release_dir)
        else:
            tmp_build_dir.mkdir(parents=True, exist_ok=True)
            try:
                # 1. Clean extraction via git archive
                tar_proc = subprocess.Popen(
                    ["git", "archive", "--format=tar", full_sha],
                    cwd=str(repo_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                untar_proc = subprocess.Popen(
                    ["tar", "-x", "-C", str(tmp_build_dir)],
                    stdin=tar_proc.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if tar_proc.stdout:
                    tar_proc.stdout.close()

                _, untar_err = untar_proc.communicate()
                tar_err = tar_proc.wait()
                if untar_proc.returncode != 0 or tar_err != 0:
                    raise ReleaseVerificationError(
                        f"git archive export failed: {untar_err.decode('utf-8')}"
                    )

                # 2. Compute SHA-256 for all extracted files to ensure tamper detection
                file_hashes: dict[str, str] = {}
                for fpath in sorted(tmp_build_dir.rglob("*")):
                    if fpath.is_file() and fpath.name != "release_metadata.json":
                        rel_posix = fpath.relative_to(tmp_build_dir).as_posix()
                        file_hashes[rel_posix] = _compute_file_sha256(fpath)

                # 3. Write metadata.json
                metadata = {
                    "commit_sha": full_sha,
                    "short_sha": short_sha,
                    "source_ref": git_ref,
                    "tree_hash": tree_hash,
                    "installed_at": datetime.now(UTC).isoformat(),
                    "source_repo": str(repo_root),
                    "file_hashes": file_hashes,
                }
                (tmp_build_dir / "release_metadata.json").write_text(
                    json.dumps(metadata, indent=2), encoding="utf-8"
                )

                # 4. Atomic rename to target_release_dir
                os.replace(tmp_build_dir, target_release_dir)
            except Exception as exc:
                if tmp_build_dir.exists():
                    shutil.rmtree(tmp_build_dir, ignore_errors=True)
                raise ReleaseVerificationError(f"Failed installing release {full_sha}: {exc}") from exc

        # Clean any accidental bytecode caches before venv preparation
        for p in target_release_dir.glob("**/__pycache__"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)

        # 4. Handle isolated virtual environment if requested
        venv_path: Path | None = None
        if create_venv:
            venv_path = self.venvs_root / full_sha
            self.prepare_and_verify_release_venv(
                release_path=target_release_dir,
                venv_path=venv_path,
                python_executable=python_executable,
            )

        # 5. Atomically activate as stable if requested
        if activate_as_stable:
            return self.promote_release(full_sha, repo_root=repo_root)

        is_active = self.is_release_active(full_sha)
        return ReleaseRecord(
            commit_sha=full_sha,
            short_sha=short_sha,
            source_ref=git_ref,
            installed_at=datetime.now(UTC).isoformat(),
            release_path=target_release_dir,
            is_active_stable=is_active,
            tree_hash=tree_hash,
            venv_path=venv_path,
        )

    def prepare_and_verify_release_venv(
        self,
        release_path: Path,
        venv_path: Path,
        python_executable: str | None = None,
    ) -> None:
        """Prepares an isolated virtual environment and verifies self-contained imports.

        Guarantees:
        - Bootstraps Python virtual environment using specified or active Python interpreter.
        - Populates required third-party runtime dependencies into release site-packages.
        - Binds the release source root to the environment via .pth file.
        - Executes an isolated verification script confirming the release MCP server
          and its dependencies load cleanly without PYTHONPATH or Development leakage.
        """
        py_bin = python_executable or sys.executable
        if not venv_path.exists():
            subprocess.check_call(
                [py_bin, "-m", "venv", str(venv_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        v_sites = list(venv_path.glob("lib/python*/site-packages"))
        if not v_sites:
            raise ReleaseVerificationError(f"Cannot locate site-packages in created venv at '{venv_path}'")
        v_site = v_sites[0]

        # Bind release source and enforce bytecode suppression in release venv via .pth
        (v_site / "my_agent.pth").write_text(
            f"import sys; sys.dont_write_bytecode = True\n{release_path}\n",
            encoding="utf-8",
        )

        # Replicate third-party site packages from active interpreter venv
        py_path = Path(py_bin)
        base_prefix = py_path.parent.parent
        src_sites = list((base_prefix / "lib").glob("python*/site-packages"))
        if not src_sites:
            base_prefix = py_path.resolve().parent.parent
            src_sites = list((base_prefix / "lib").glob("python*/site-packages"))

        if src_sites:
            src_site = src_sites[0]
            for item in src_site.iterdir():
                if item.name.startswith((".", "tests", "data", "docs", "_editable")):
                    continue
                if item.name in ["core", "my_agent_mcp", "persistence", "agents", "integrations", "eval", "scripts"]:
                    continue
                dest = v_site / item.name
                if not dest.exists():
                    try:
                        if item.is_dir():
                            shutil.copytree(item, dest, symlinks=True)
                        else:
                            shutil.copy2(item, dest)
                    except OSError as exc:
                        logger.debug("Failed copying site-package item '%s': %s", item.name, exc)

        # Isolated verification using the release-specific interpreter
        vpy = venv_path / "bin" / "python"
        if not vpy.exists():
            raise ReleaseVerificationError(f"Release python interpreter not found at '{vpy}'")

        test_code = (
            "import sys, mcp, core.runtime_environment, my_agent_mcp.server; "
            "rel = sys.argv[1]; "
            "srv_path = str(my_agent_mcp.server.__file__); "
            "assert srv_path.startswith(rel), f'Isolation leakage: {srv_path} is outside {rel}'; "
            "assert sys.dont_write_bytecode is True, 'sys.dont_write_bytecode not enabled in release venv'; "
            "print('OK')"
        )
        clean_env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(Path.home()),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        try:
            res = subprocess.run(
                [str(vpy), "-c", test_code, str(release_path)],
                cwd="/tmp",
                capture_output=True,
                text=True,
                env=clean_env,
                timeout=15.0,
            )
            if res.returncode != 0:
                raise ReleaseVerificationError(
                    f"Release venv verification failed (exit code {res.returncode}): {res.stderr.strip()}"
                )
        except subprocess.TimeoutExpired as exc:
            raise ReleaseVerificationError(f"Release venv verification timed out: {exc}") from exc
        except Exception as exc:
            if not isinstance(exc, ReleaseVerificationError):
                raise ReleaseVerificationError(f"Release venv verification error: {exc}") from exc
            raise

        # Ensure no bytecode caches were written into the release directory
        for p in release_path.glob("**/__pycache__"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)

    def _atomic_symlink_switch(self, target_dir: Path) -> None:
        """Atomically points self.stable_link to target_dir via a temporary symlink."""
        self.stable_link.parent.mkdir(parents=True, exist_ok=True)
        tmp_link = self.stable_link.parent / f".tmp_link_{uuid4().hex[:8]}"
        try:
            os.symlink(target_dir, tmp_link)
            os.replace(tmp_link, self.stable_link)
        finally:
            if tmp_link.exists() or os.path.islink(tmp_link):
                try:
                    tmp_link.unlink()
                except OSError:
                    pass

    def is_release_active(self, commit_sha: str) -> bool:
        """Checks if the given commit_sha is currently targeted by self.stable_link."""
        if not self.stable_link.exists() and not os.path.islink(self.stable_link):
            return False
        try:
            resolved = self.stable_link.resolve()
            return resolved.name == commit_sha or resolved == (self.releases_root / commit_sha)
        except Exception:
            return False

    def list_releases(self) -> list[ReleaseRecord]:
        """Lists all installed releases ordered by installed_at descending."""
        records: list[ReleaseRecord] = []
        if not self.releases_root.exists():
            return records

        for entry in self.releases_root.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            meta_file = entry / "release_metadata.json"
            if meta_file.exists():
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    sha = data.get("commit_sha", entry.name)
                    rec = ReleaseRecord(
                        commit_sha=sha,
                        short_sha=data.get("short_sha", sha[:7]),
                        source_ref=data.get("source_ref", "unknown"),
                        installed_at=data.get("installed_at", ""),
                        release_path=entry,
                        is_active_stable=self.is_release_active(sha),
                        tree_hash=data.get("tree_hash"),
                        venv_path=(self.venvs_root / sha) if (self.venvs_root / sha).exists() else None,
                    )
                    records.append(rec)
                except Exception as exc:
                    logger.debug("Failed reading release entry '%s': %s", entry, exc)

        records.sort(key=lambda r: r.installed_at, reverse=True)
        return records

    def promote_release(
        self,
        target_commit_sha_or_ref: str,
        repo_root: Path | None = None,
    ) -> ReleaseRecord:
        """Explicitly promotes an already staged release to become active Stable."""
        clean_target = target_commit_sha_or_ref.strip().lower()
        releases = self.list_releases()
        target_release: ReleaseRecord | None = None

        for r in releases:
            if r.commit_sha.lower() == clean_target or r.short_sha.lower() == clean_target:
                target_release = r
                break

        if target_release is None and repo_root is not None:
            try:
                full_sha, _, _ = self.resolve_git_commit(target_commit_sha_or_ref, repo_root)
                for r in releases:
                    if r.commit_sha == full_sha:
                        target_release = r
                        break
            except Exception:
                pass

        if target_release is None:
            raise ReleaseVerificationError(
                f"Release '{target_commit_sha_or_ref}' is not installed in '{self.releases_root}'. "
                "You must stage the release before promoting."
            )

        # Verify integrity before promotion
        integrity = self.verify_release_integrity(target_release.release_path)
        if not integrity.get("valid"):
            raise ReleaseVerificationError(
                f"Release at '{target_release.release_path}' failed integrity verification: {integrity.get('reason')}"
            )

        # Atomically switch symlink
        self._atomic_symlink_switch(target_release.release_path)
        logger.info("Successfully promoted release '%s' to active Stable", target_release.commit_sha)

        return ReleaseRecord(
            commit_sha=target_release.commit_sha,
            short_sha=target_release.short_sha,
            source_ref=target_release.source_ref,
            installed_at=target_release.installed_at,
            release_path=target_release.release_path,
            is_active_stable=True,
            tree_hash=target_release.tree_hash,
            venv_path=target_release.venv_path,
        )

    def rollback_release(self, target_commit_sha: str | None = None) -> ReleaseRecord:
        """Rolls back stable_link to a prior release without mutating persistent data."""
        releases = self.list_releases()
        if not releases:
            raise ReleaseVerificationError("No installed releases available to rollback to.")

        target_release: ReleaseRecord | None = None
        if target_commit_sha:
            clean_target = target_commit_sha.strip().lower()
            for r in releases:
                if r.commit_sha.lower() == clean_target or r.short_sha.lower() == clean_target:
                    target_release = r
                    break
            if target_release is None:
                raise ReleaseVerificationError(
                    f"Requested rollback target '{target_commit_sha}' was not found in installed releases."
                )
        else:
            # Find the first release that is not currently active
            for r in releases:
                if not r.is_active_stable:
                    target_release = r
                    break
            if target_release is None:
                raise ReleaseVerificationError(
                    "No prior release found for rollback. Only one release is installed."
                )

        # Verify integrity before rollback switch
        integrity = self.verify_release_integrity(target_release.release_path)
        if not integrity.get("valid"):
            raise ReleaseVerificationError(
                f"Rollback target '{target_release.commit_sha}' failed integrity verification: {integrity.get('reason')}"
            )

        # Atomically switch symlink
        self._atomic_symlink_switch(target_release.release_path)
        logger.info("Successfully rolled back Stable release to '%s'", target_release.commit_sha)

        return ReleaseRecord(
            commit_sha=target_release.commit_sha,
            short_sha=target_release.short_sha,
            source_ref=target_release.source_ref,
            installed_at=target_release.installed_at,
            release_path=target_release.release_path,
            is_active_stable=True,
            tree_hash=target_release.tree_hash,
            venv_path=target_release.venv_path,
        )

    def verify_release_integrity(self, release_path: Path) -> dict[str, Any]:
        """Validates release structure, cryptographic file hashes, and lack of dev caches."""
        path = release_path.expanduser().resolve()
        if not path.exists():
            return {"valid": False, "reason": "Path does not exist"}

        meta_file = path / "release_metadata.json"
        if not meta_file.exists():
            return {"valid": False, "reason": "Missing release_metadata.json"}

        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"valid": False, "reason": f"Corrupt release_metadata.json: {exc}"}

        commit_sha = data.get("commit_sha")
        if not commit_sha:
            return {"valid": False, "reason": "Missing 'commit_sha' in release_metadata.json"}
        if str(commit_sha).lower() != path.name.lower():
            return {
                "valid": False,
                "reason": f"Manifest commit_sha '{commit_sha}' does not match release directory '{path.name}'",
            }

        # Check required directories
        required_dirs = ["core", "my_agent_mcp", "persistence"]
        missing_dirs = [d for d in required_dirs if not (path / d).is_dir()]
        if missing_dirs:
            return {"valid": False, "reason": f"Missing core directories: {missing_dirs}"}

        # Check for prohibited dev caches
        prohibited_artifacts = [".git", ".pytest_cache", ".ruff_cache", ".mypy_cache"]
        found_prohibited = [p for p in prohibited_artifacts if (path / p).exists()]
        if found_prohibited:
            return {"valid": False, "reason": f"Prohibited dev artifacts found in release: {found_prohibited}"}

        # Cryptographic content verification: verify SHA-256 for all staged files
        expected_hashes = data.get("file_hashes")
        if not expected_hashes or not isinstance(expected_hashes, dict):
            return {"valid": False, "reason": "Missing or empty 'file_hashes' manifest in release_metadata.json"}

        for rel_str, expected_hash in expected_hashes.items():
            fpath = path / rel_str
            if not fpath.exists():
                return {
                    "valid": False,
                    "reason": f"TAMPERING_DETECTED: Missing release file '{rel_str}'",
                }
            actual_hash = _compute_file_sha256(fpath)
            if actual_hash != expected_hash:
                return {
                    "valid": False,
                    "reason": f"TAMPERING_DETECTED: Content checksum mismatch on '{rel_str}'",
                }

        # Check for unauthorized newly injected files
        for fpath in path.rglob("*"):
            if fpath.is_file() and fpath.name != "release_metadata.json":
                rel = fpath.relative_to(path).as_posix()
                if rel not in expected_hashes:
                    return {
                        "valid": False,
                        "reason": f"TAMPERING_DETECTED: Unauthorized file '{rel}' injected into release",
                    }

        return {"valid": True, "release_path": str(path)}
