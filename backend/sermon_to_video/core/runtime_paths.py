from __future__ import annotations

from pathlib import Path
from typing import Any


BUILD_DIR_NAME = "build"


def resolve_render_paths(project_dir: Path, output_file: Path | None = None) -> tuple[Path, Path, Path]:
    project_dir = project_dir.resolve()
    storyboard_path = project_dir / "storyboard.json"
    build_dir = project_dir / BUILD_DIR_NAME
    final_output = output_file.resolve() if output_file else (project_dir / "final_video.mp4")
    return storyboard_path, build_dir, final_output


def can_reuse_cache(use_cache: bool, *paths: Path) -> bool:
    return bool(use_cache and paths and all(path.exists() for path in paths))


def resolve_scene_output_for_concat(
    item: dict[str, Any],
    work_dir: Path,
    *,
    use_cache: bool,
    phase4_ran: bool,
    selected_scene_id: int | None = None,
) -> str:
    scene_id = item.get("scene_id")
    explicit_path = item.get("final_scene_filepath")
    if explicit_path:
        return str(explicit_path)

    candidate = work_dir / f"scene_{scene_id}_final.mp4"

    # If phase 4 already ran in this invocation with cache disabled, do not silently
    # fall back to stale build outputs for scenes that should have been regenerated.
    should_have_been_rendered = selected_scene_id is None or scene_id == selected_scene_id
    if phase4_ran and not use_cache and should_have_been_rendered:
        raise RuntimeError(
            f"Scene {scene_id} is missing an in-memory final_scene_filepath after phase 4. "
            "Refusing to reuse stale build output with cache disabled."
        )

    if candidate.exists():
        item["final_scene_filepath"] = str(candidate)
        return str(candidate)

    raise RuntimeError(
        f"Scene {scene_id} final output is missing. Expected {candidate}. "
        "Run with --start-phase 4 to regenerate scene assemblies."
    )
