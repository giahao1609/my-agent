from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import BinaryIO

from .handoff_contracts import VisualDiffResult


class VisualDiffValidator:
    """Pure-Python pixel-level visual regression comparison engine for PNG screenshots."""

    PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

    @classmethod
    def _parse_png(cls, raw_bytes: bytes) -> tuple[int, int, bytes] | None:
        """Parses PNG header and extracts (width, height, decompressed_scanlines)."""
        if not raw_bytes.startswith(cls.PNG_SIGNATURE):
            return None

        offset = 8
        width = 0
        height = 0
        idat_chunks: list[bytes] = []

        while offset + 8 <= len(raw_bytes):
            chunk_len = struct.unpack(">I", raw_bytes[offset : offset + 4])[0]
            chunk_type = raw_bytes[offset + 4 : offset + 8]
            data_start = offset + 8
            data_end = data_start + chunk_len
            offset = data_end + 4  # skip data + CRC

            if chunk_type == b"IHDR":
                width, height = struct.unpack(">II", raw_bytes[data_start : data_start + 8])
            elif chunk_type == b"IDAT":
                idat_chunks.append(raw_bytes[data_start:data_end])
            elif chunk_type == b"IEND":
                break

        if not idat_chunks or width == 0 or height == 0:
            return None

        try:
            decompressed = zlib.decompress(b"".join(idat_chunks))
            return width, height, decompressed
        except Exception:
            return None

    @classmethod
    def compare_images(
        cls,
        baseline_path: Path | str,
        current_path: Path | str,
        tolerance_percentage: float = 0.5,
    ) -> VisualDiffResult:
        base_p = Path(baseline_path)
        curr_p = Path(current_path)

        if not base_p.exists() or not curr_p.exists():
            return VisualDiffResult(
                baseline_path=str(base_p),
                current_path=str(curr_p),
                total_pixels=0,
                differing_pixels=0,
                diff_percentage=100.0,
                tolerance_percentage=tolerance_percentage,
                passed=False,
                summary=f"Visual diff failed: One or both image files do not exist ({base_p}, {curr_p}).",
            )

        raw_base = base_p.read_bytes()
        raw_curr = curr_p.read_bytes()

        # 1. Byte-level exact match
        if raw_base == raw_curr:
            return VisualDiffResult(
                baseline_path=str(base_p),
                current_path=str(curr_p),
                total_pixels=1,
                differing_pixels=0,
                diff_percentage=0.0,
                tolerance_percentage=tolerance_percentage,
                passed=True,
                summary="Visual diff PASSED: Images are 100% byte-identical (0.0% difference).",
            )

        # 2. PNG pixel-level analysis
        png_base = cls._parse_png(raw_base)
        png_curr = cls._parse_png(raw_curr)

        if png_base is not None and png_curr is not None:
            w1, h1, scanlines1 = png_base
            w2, h2, scanlines2 = png_curr

            if w1 != w2 or h1 != h2:
                total_px = max(w1 * h1, w2 * h2)
                return VisualDiffResult(
                    baseline_path=str(base_p),
                    current_path=str(curr_p),
                    total_pixels=total_px,
                    differing_pixels=total_px,
                    diff_percentage=100.0,
                    tolerance_percentage=tolerance_percentage,
                    passed=False,
                    summary=f"Visual regression FAILED: Dimension mismatch ({w1}x{h1} vs {w2}x{h2}).",
                )

            total_px = w1 * h1
            bytes_per_scanline = len(scanlines1) // h1 if h1 > 0 else 0
            bpp = max(1, (bytes_per_scanline - 1) // w1) if w1 > 0 else 3

            differing_px = 0
            for y in range(h1):
                row_start = y * bytes_per_scanline + 1  # skip filter byte
                for x in range(w1):
                    px_start = row_start + x * bpp
                    px1 = scanlines1[px_start : px_start + bpp]
                    px2 = scanlines2[px_start : px_start + bpp]
                    if px1 != px2:
                        differing_px += 1

            diff_pct = (differing_px / total_px) * 100.0 if total_px > 0 else 0.0
        else:
            # Fallback raw byte difference
            max_len = max(len(raw_base), len(raw_curr))
            min_len = min(len(raw_base), len(raw_curr))
            diff_bytes = sum(1 for i in range(min_len) if raw_base[i] != raw_curr[i]) + (max_len - min_len)
            total_px = max_len
            differing_px = diff_bytes
            diff_pct = (diff_bytes / max_len) * 100.0 if max_len > 0 else 0.0

        passed = diff_pct <= tolerance_percentage
        summary = (
            f"Visual diff PASSED: {diff_pct:.2f}% pixel difference within {tolerance_percentage}% tolerance."
            if passed
            else f"Visual regression FAILED: {diff_pct:.2f}% pixel difference exceeds {tolerance_percentage}% tolerance."
        )

        return VisualDiffResult(
            baseline_path=str(base_p),
            current_path=str(curr_p),
            total_pixels=total_px,
            differing_pixels=differing_px,
            diff_percentage=round(diff_pct, 2),
            tolerance_percentage=tolerance_percentage,
            passed=passed,
            summary=summary,
        )
