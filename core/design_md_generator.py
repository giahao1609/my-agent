"""DESIGN.md generator — writes and reads design contracts for UiCoderAgent.

Generates a project-level DESIGN.md file from a DesignStyleProfile.
This file serves as the persistent design contract that UiCoderAgent reads
on every subsequent UI task to maintain visual consistency.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import PROJECT_ROOT
from .runtime_environment import (
    assert_not_in_immutable_release,
    resolve_runtime_environment,
)
from .ui_design_style_engine import DesignStyleProfile, UiDesignStyleEngine, DesignContext

DEFAULT_DESIGNS_DIR = PROJECT_ROOT / "data" / "designs"


def get_runtime_designs_dir() -> Path:
    """Returns the active runtime's persistent designs directory."""
    return resolve_runtime_environment().designs_dir


class DesignMdGenerator:
    """Generates and reads DESIGN.md files for workspace design contracts.

    Usage::

        engine = UiDesignStyleEngine()
        context = engine.analyze_context(description="...", sector="portfolio")
        profile = engine.generate_style_profile(context)

        gen = DesignMdGenerator()
        path = gen.write_to_agent_repo(profile, project_id="my-project")
        # -> <runtime_data_dir>/designs/my-project.design.md (isolated from user repo and immutable releases)
    """

    FILENAME = "DESIGN.md"

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._storage_dir = Path(storage_dir).expanduser().resolve() if storage_dir is not None else None

    def _resolve_storage_dir(self, storage_dir: Path | None = None) -> Path:
        if storage_dir is not None:
            return Path(storage_dir).expanduser().resolve()
        if self._storage_dir is not None:
            return self._storage_dir
        return get_runtime_designs_dir()

    def generate(self, profile: DesignStyleProfile, project_name: str = "Project") -> str:
        """Generate DESIGN.md markdown content from a DesignStyleProfile."""
        return profile.to_design_md(project_name=project_name)

    def write_to_agent_repo(
        self,
        profile: DesignStyleProfile,
        project_id: str,
        project_name: str = "Project",
        storage_dir: Path | None = None,
    ) -> Path:
        """Write DESIGN.md to MyAgent's runtime data directory, isolated from user repos and immutable releases."""
        target_dir = self._resolve_storage_dir(storage_dir)
        assert_not_in_immutable_release(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in project_id)
        output_path = target_dir / f"{safe_id}.design.md"
        content = self.generate(profile, project_name=project_name)
        output_path.write_text(content, encoding="utf-8")
        return output_path

    def read_from_agent_repo(
        self,
        project_id: str,
        storage_dir: Path | None = None,
    ) -> str | None:
        """Read design contract from MyAgent's runtime data directory."""
        target_dir = self._resolve_storage_dir(storage_dir)
        safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in project_id)
        path = target_dir / f"{safe_id}.design.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_to_workspace(
        self,
        profile: DesignStyleProfile,
        workspace: Path,
        project_name: str = "Project",
    ) -> Path:
        """Write DESIGN.md to workspace root. Returns the path written."""
        content = self.generate(profile, project_name=project_name)
        output_path = workspace / self.FILENAME
        output_path.write_text(content, encoding="utf-8")
        return output_path

    def read_from_workspace(self, workspace: Path) -> str | None:
        """Read DESIGN.md content from workspace root. Returns None if not found."""
        path = workspace / self.FILENAME
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def workspace_has_design_md(self, workspace: Path) -> bool:
        """Check if a DESIGN.md exists in the workspace."""
        return (workspace / self.FILENAME).exists()

    def read_profile_from_workspace(
        self,
        workspace: Path,
        engine: UiDesignStyleEngine,
        fallback_sector: str = "saas_b2b",
    ) -> DesignStyleProfile | None:
        """Read DESIGN.md and reconstruct a DesignStyleProfile from it.

        Parses the visual language from the DESIGN.md header and re-generates
        the profile using the engine. Returns None if DESIGN.md does not exist.

        Note: This reconstructs the profile from the stored visual language name.
        Full token fidelity is preserved since tokens are written verbatim in DESIGN.md.
        For an exact reconstruction, the original DesignContext should be stored separately.
        """
        content = self.read_from_workspace(workspace)
        if content is None:
            return None

        # Extract visual language from DESIGN.md
        vl_match = re.search(r"(?:\*\*Visual Language:\*\*\s*([^\n\r]+)|Visual Language:\s*\*\*([^\*]+)\*\*|\*\*([^\*]+)\*\*)", content)
        if not vl_match:
            return None

        visual_language = (vl_match.group(1) or vl_match.group(2) or vl_match.group(3) or "").strip()

        # Extract surface mode and layout paradigm if present
        sm_match = re.search(r"\*\*Surface Mode:\*\*\s*([^\n\r]+)", content)
        surface_mode = sm_match.group(1).strip() if sm_match else "earn"

        lp_match = re.search(r"\*\*Layout Paradigm:\*\*\s*([^\n\r]+)", content)
        layout_paradigm = lp_match.group(1).strip() if lp_match else None

        # Map visual language back to a representative sector
        _VL_TO_SECTOR = {
            "Architectural Minimalism": "fintech",
            "Editorial Avant-garde": "creative_agency",
            "Organic Minimalism": "health_wellness",
            "Structured Clarity": "saas_b2b",
            "Neo-Brutalism": "portfolio",
            "Tactile Editorial": "ecommerce",
            "Monochrome Technical": "developer_tool",
            "Kinetic Playful": "startup_consumer",
            "Warm Academic": "education",
            "Precision Dashboard": "data_analytics",
            "Cognitive Focus & Modern Clarity": "edtech_ai",
            "Cyber Dark & Neon Accent": "web3_crypto",
            "Expressive Social": "social_community",
        }
        sector = _VL_TO_SECTOR.get(visual_language, fallback_sector)

        context = engine.analyze_context(
            description=f"Reconstructed from DESIGN.md (visual language: {visual_language})",
            sector=sector,
            surface_mode=surface_mode,
            layout_paradigm=layout_paradigm,
        )
        return engine.generate_style_profile(context)
