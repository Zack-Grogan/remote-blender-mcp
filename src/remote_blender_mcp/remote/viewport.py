"""Operate the actual GUI viewport on Blender's main thread."""

import math
import uuid
from contextlib import contextmanager

import bpy
import gpu
from mathutils import Euler, Vector

from common import ROOT, workspace_path


def context():
    if bpy.app.background:
        raise ValueError('Viewport tools require create_session(mode="gui")')
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next(r for r in area.regions if r.type == "WINDOW")
                return window, area, region
    raise ValueError("No 3D viewport exists in the current Blender screen")


@contextmanager
def immediate_view():
    # Tool results describe the final view, not an in-flight UI transition.
    preferences = bpy.context.preferences.view
    previous = preferences.smooth_view
    preferences.smooth_view = 0
    try:
        yield
    finally:
        preferences.smooth_view = previous


def fit_selection(area, region):
    """Fit evaluated world bounds and verify their projection in the actual region."""
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for original in bpy.context.selected_objects:
        obj = original.evaluated_get(depsgraph)
        if obj.type == "ARMATURE" and obj.pose:
            points.extend(
                obj.matrix_world @ endpoint for bone in obj.pose.bones for endpoint in (bone.head, bone.tail)
            )
        elif any(tuple(corner) != (-1, -1, -1) for corner in obj.bound_box):
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
        else:
            points.append(obj.matrix_world.translation.copy())
    if not points:
        raise ValueError("Selection has no bounds to frame")
    low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    view = area.spaces.active.region_3d
    view.view_location = (low + high) * 0.5
    view.view_distance = max(view.view_distance, 0.1)
    margin = min(region.width, region.height) * 0.05
    for iteration in range(32):
        view.update()
        projected = [location_3d_to_region_2d(region, view, p) for p in points]
        if all(
            p is not None
            and margin <= p.x <= region.width - margin
            and margin <= p.y <= region.height - margin
            for p in projected
        ):
            return dict(points=len(points), zoom_steps=iteration, margin_pixels=margin)
        view.view_distance *= 1.25
    raise ValueError("Cannot fit selection in this viewport; inspect its clipping planes and view settings")


def control(params):
    window, area, region = context()
    space = area.spaces.active
    with bpy.context.temp_override(window=window, area=area, region=region), immediate_view():
        if params.get("frame") is not None:
            bpy.context.scene.frame_set(params["frame"])
        bpy.context.view_layer.update()
        names = params.get("select")
        if names is not None:
            missing = [name for name in names if name not in bpy.context.view_layer.objects]
            if missing:
                raise ValueError(f"Objects not in the current view layer: {missing}")
            if bpy.context.object and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for obj in bpy.context.view_layer.objects:
                obj.select_set(obj.name in names)
            bpy.context.view_layer.objects.active = bpy.data.objects[names[0]] if names else None
        view = params.get("view")
        if view in ("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"):
            bpy.ops.view3d.view_axis(type=view, align_active=False)
        elif view == "CAMERA":
            if not bpy.context.scene.camera:
                raise ValueError("Scene has no camera")
            space.region_3d.view_perspective = "CAMERA"
        elif view == "ISO":
            space.region_3d.view_perspective = "PERSP"
            space.region_3d.view_rotation = Euler(
                (math.radians(65), 0, math.radians(45)), "XYZ"
            ).to_quaternion()
        if params.get("frame_selected"):
            if not bpy.context.selected_objects:
                raise ValueError("Select at least one object before framing")
            bpy.context.view_layer.update()
            bpy.ops.view3d.view_selected(use_all_regions=False)
        if params.get("target") is not None:
            space.region_3d.view_location = params["target"]
        if params.get("distance") is not None:
            space.region_3d.view_distance = params["distance"]
        if params.get("rotation") is not None:
            space.region_3d.view_rotation = Euler(
                tuple(math.radians(a) for a in params["rotation"]), "XYZ"
            ).to_quaternion()
            space.region_3d.view_perspective = "PERSP"
        if params.get("shading"):
            space.shading.type = params["shading"]
        if params.get("overlays") is not None:
            space.overlay.show_overlays = params["overlays"]
        framing = None
        if (
            params.get("frame_selected")
            and params.get("target") is None
            and params.get("distance") is None
            and space.region_3d.view_perspective != "CAMERA"
        ):
            framing = fit_selection(area, region)
        space.region_3d.update()
        area.tag_redraw()
        return dict(
            framing=framing,
            view_perspective=space.region_3d.view_perspective,
            target=list(space.region_3d.view_location),
            distance=space.region_3d.view_distance,
            rotation=list(space.region_3d.view_rotation),
            shading=space.shading.type,
            selected=[o.name for o in bpy.context.selected_objects],
            frame=bpy.context.scene.frame_current,
            graphics_renderer=gpu.platform.renderer_get(),
            graphics_vendor=gpu.platform.vendor_get(),
            graphics_version=gpu.platform.version_get(),
        )


def screenshot():
    window, area, region = context()
    path = workspace_path(f"artifacts/viewport-{uuid.uuid4().hex}.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    with bpy.context.temp_override(window=window, area=area, region=region):
        from capture import capture

        capture(path)
    return dict(
        path=str(path.relative_to(ROOT)),
        source="virtual_display",
        width=window.width,
        height=window.height,
        graphics_renderer=gpu.platform.renderer_get(),
    )
