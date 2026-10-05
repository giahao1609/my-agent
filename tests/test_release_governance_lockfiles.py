import json
import tempfile
from pathlib import Path
from core.release_governance import SBOMGenerator


def test_sbom_generator_with_lockfiles():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Node package-lock.json
        pkg_lock = tmp_path / "package-lock.json"
        pkg_lock.write_text(json.dumps({
            "name": "test-app",
            "version": "1.0.0",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "test-app", "version": "1.0.0"},
                "node_modules/express": {"version": "4.19.2", "license": "MIT"},
                "node_modules/lodash": {"version": "4.17.21", "license": "MIT"}
            }
        }), encoding="utf-8")

        # 2. Go sum
        go_sum = tmp_path / "go.sum"
        go_sum.write_text("""
github.com/gin-gonic/gin v1.9.1 h1:4AODfdAKGqNVWQR44o6NpUfpOmCUJDiVKupCoaciez4=
github.com/gin-gonic/gin v1.9.1/go.mod h1:hPrL7YrpUBlQdfOG195BzIdif5OOjv56YvY4Y3Tiw54=
""", encoding="utf-8")

        # 3. Rust Cargo.lock
        cargo_lock = tmp_path / "Cargo.lock"
        cargo_lock.write_text("""
[[package]]
name = "serde"
version = "1.0.197"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "tokio"
version = "1.36.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
""", encoding="utf-8")

        generator = SBOMGenerator()
        manifest = generator.generate(tmp_path, project_name="multi-eco-project")

        names = {c.name: c for c in manifest.components}
        assert "express" in names
        assert names["express"].ecosystem == "npm"
        assert names["express"].version == "4.19.2"

        assert "github.com/gin-gonic/gin" in names
        assert names["github.com/gin-gonic/gin"].ecosystem == "gomod"

        assert "serde" in names
        assert names["serde"].ecosystem == "cargo"
        assert names["serde"].version == "1.0.197"
