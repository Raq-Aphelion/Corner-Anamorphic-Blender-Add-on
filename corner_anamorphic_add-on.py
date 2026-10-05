bl_info = {
    "name": "Corner Anamorphic Screen Rig",
    "version": (1, 7, 0),
    "blender": (4, 2, 0),
    "author": "Raq-Aphelion",
    "location": "View3D > Sidebar > Corner Anamorphic",
    "description": "Builds and renders a two-screen 90-degree anamorphic corner installation from one viewer camera.",
    "category": "3D View",
}

import bpy
import math
import os
import re
import mathutils
from mathutils import Vector

from bpy.app.handlers import persistent
from bpy_extras.object_utils import world_to_camera_view


# ============================================================================
# Constants
# ============================================================================

COLL_NAME = "CA_RIG"

OBJ_MASTER = "CA_MASTER_CAMERA"
# Screen names are from the VIEWER's perspective, looking at the outside
# of the corner:
#   LEFT  = screen extending along world +Y
#   RIGHT = screen extending along world +X
OBJ_LEFT = "CA_SCREEN_LEFT"
OBJ_RIGHT = "CA_SCREEN_RIGHT"

MAT_PROJECTION = "CA_PROJECTION_MAT"
UV_PROJECTED = "CA_PROJECTED"

IMG_MASTER = "CA_MASTER_IMAGE"
IMG_MASTER_SEQ = "CA_MASTER_SEQUENCE"

SCENE_OUT_LEFT = "CA_OUTPUT_LEFT"
SCENE_OUT_RIGHT = "CA_OUTPUT_RIGHT"
SCENE_OUT_COMBINED = "CA_OUTPUT_COMBINED"

OBJ_COMBINED = "CA_COMBINED_CAPTURE"


# ============================================================================
# Render progress tracking
# ============================================================================

CA_PROGRESS = {
    "active": False,
    "phase": "",
    "done": 0,
    "total": 0,
    "stats": "",
    "message": "Idle"
}


def progress_begin(phase, total):
    CA_PROGRESS.update(
        active=True,
        phase=phase,
        done=0,
        total=max(1, total),
        stats="",
        message=""
    )


def progress_finish(message):
    CA_PROGRESS.update(
        active=False,
        message=message
    )
    tag_ui_redraw()


def tag_ui_redraw():
    """
    Repaint every area so the panel's progress display follows
    the render handlers while a modal render is running.
    """

    wm = bpy.context.window_manager

    if wm is None:
        return

    for window in wm.windows:

        if window.screen is None:
            continue

        for area in window.screen.areas:
            area.tag_redraw()


# ============================================================================
# Async render jobs
# ============================================================================
#
# Renders are started with INVOKE_DEFAULT so Blender shows its render
# window with the progress bar and the UI stays responsive.  That call
# returns immediately, so the work that depends on a finished render
# (restoring the crop border, loading the master frames, restoring the
# user's scene) runs as queued steps driven by a timer, gated on the
# render_complete / render_cancel handlers.

CA_JOB = {
    "steps": [],
    "in_flight": False,
    "cancelled": False,
    "after_render": None,
    "cleanup": None
}


def job_running():
    return bool(CA_JOB["steps"]) or CA_JOB["in_flight"]


def run_after_render():
    fn = CA_JOB["after_render"]
    CA_JOB["after_render"] = None

    if fn is not None:

        try:
            fn()
        except Exception:
            pass


def start_render(after_render=None, **kwargs):
    """
    Start a non-blocking render.  after_render runs when the render
    completes OR is cancelled (crop border restore, output setting
    restore).
    """

    CA_JOB["in_flight"] = True
    CA_JOB["after_render"] = after_render

    try:
        bpy.ops.render.render('INVOKE_DEFAULT', **kwargs)

    except Exception:
        CA_JOB["in_flight"] = False
        run_after_render()
        raise


def start_job(steps, cleanup=None):
    CA_JOB["steps"] = list(steps)
    CA_JOB["cancelled"] = False
    CA_JOB["cleanup"] = cleanup

    bpy.app.timers.register(
        ca_job_timer,
        first_interval=0.1
    )


def finish_job(message):
    CA_JOB["steps"] = []
    CA_JOB["cancelled"] = False

    cleanup = CA_JOB["cleanup"]
    CA_JOB["cleanup"] = None

    if cleanup is not None:

        try:
            cleanup()
        except Exception:
            pass

    progress_finish(message)


def ca_job_timer():

    if CA_JOB["in_flight"]:
        return 0.1

    if CA_JOB["cancelled"]:
        finish_job("Cancelled.")
        return None

    if not CA_JOB["steps"]:
        return None

    step = CA_JOB["steps"].pop(0)

    try:
        step()

    except Exception as exc:
        print(f"Corner Anamorphic render job failed: {exc}")
        finish_job(f"Failed: {exc}")
        return None

    return 0.1


@persistent
def ca_render_pre(scene):
    CA_PROGRESS["active"] = True
    CA_PROGRESS["stats"] = ""
    tag_ui_redraw()


@persistent
def ca_render_post(scene):
    if CA_PROGRESS["active"]:
        CA_PROGRESS["done"] += 1
    tag_ui_redraw()


@persistent
def ca_render_complete(scene):
    CA_PROGRESS["active"] = False
    CA_JOB["in_flight"] = False
    run_after_render()
    tag_ui_redraw()


@persistent
def ca_render_cancel(scene):
    CA_PROGRESS["active"] = False
    CA_PROGRESS["message"] = "Cancelled."
    CA_JOB["in_flight"] = False
    CA_JOB["cancelled"] = True
    run_after_render()
    tag_ui_redraw()


@persistent
def ca_render_stats(*args):
    """
    Track the current frame's own progress (e.g. 'Rendering
    25 / 64 samples') so the panel can mirror the per-frame
    percentage shown in Blender's status bar instead of the
    whole-animation fraction.
    """

    if args and isinstance(args[0], str):
        CA_PROGRESS["stats"] = args[0]
        tag_ui_redraw()


def frame_progress_fraction():
    """
    Progress of the frame currently being rendered, parsed from
    the render statistics, or None when no statistics have come
    in yet.
    """

    matches = re.findall(
        r"(\d+)\s*/\s*(\d+)",
        CA_PROGRESS["stats"]
    )

    if not matches:
        return None

    done, total = (int(v) for v in matches[-1])

    if total <= 0:
        return None

    return done / total


CA_PROGRESS_HANDLERS = (
    (bpy.app.handlers.render_pre, ca_render_pre),
    (bpy.app.handlers.render_post, ca_render_post),
    (bpy.app.handlers.render_complete, ca_render_complete),
    (bpy.app.handlers.render_cancel, ca_render_cancel),
    (bpy.app.handlers.render_stats, ca_render_stats),
)


# ============================================================================
# Utility
# ============================================================================

def get_collection(scene):
    coll = bpy.data.collections.get(COLL_NAME)

    if coll is None:
        coll = bpy.data.collections.new(COLL_NAME)
        scene.collection.children.link(coll)

    elif coll.name not in scene.collection.children:
        try:
            scene.collection.children.link(coll)
        except RuntimeError:
            pass

    return coll


def unlink_from_all_collections(obj):
    for collection in list(obj.users_collection):
        collection.objects.unlink(obj)


def link_only(obj, coll):
    unlink_from_all_collections(obj)
    coll.objects.link(obj)


def remove_object(name):
    obj = bpy.data.objects.get(name)

    if obj:
        bpy.data.objects.remove(obj, do_unlink=True)


def look_at(obj, target, track_axis='-Z', up_axis='Y'):
    direction = Vector(target) - obj.location

    if direction.length < 1e-8:
        return

    obj.rotation_euler = direction.to_track_quat(
        track_axis,
        up_axis
    ).to_euler()


def update_scene_graph(scene):
    """
    Make sure matrix_world reflects the current transforms before
    any modifier or projection evaluation runs.
    """

    for view_layer in scene.view_layers:
        view_layer.update()


def ffmpeg_available():
    """
    True when this Blender build can write video files.  The
    Microsoft Store package ships without FFmpeg, so MP4 output
    can never work there.
    """

    probe = bpy.data.scenes.new("CA_FFMPEG_PROBE")

    try:

        settings = probe.render.image_settings

        if hasattr(settings, "media_type"):
            settings.media_type = 'VIDEO'

        settings.file_format = 'FFMPEG'

        return True

    except (TypeError, AttributeError):
        return False

    finally:
        bpy.data.scenes.remove(probe)


def show_ffmpeg_warning_popup():
    """
    Deferred probe + popup, run via a timer.  Registration itself
    must not touch bpy.data (it is restricted while the extension
    installer enables the add-on), so both the FFmpeg check and
    the popup live here, where the context is unrestricted again.
    """

    try:

        if not ffmpeg_available():
            bpy.ops.corner_anamorphic.ffmpeg_warning(
                'INVOKE_DEFAULT'
            )

    except Exception:
        pass

    # Returning None unregisters the timer.
    return None


# ============================================================================
# Camera / screen creation
# ============================================================================

def ensure_camera(name, coll):
    obj = bpy.data.objects.get(name)

    if obj is None or obj.type != 'CAMERA':

        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)

        data = bpy.data.cameras.new(name + "_DATA")
        obj = bpy.data.objects.new(name, data)

        coll.objects.link(obj)

    else:
        link_only(obj, coll)

    return obj


def make_grid(
    name,
    width,
    height,
    rows,
    cols,
    transform,
    coll,
    x_offset=0.0
):
    """
    Create a subdivided planar mesh in local XY and transform it into
    world space.
    """

    old = bpy.data.objects.get(name)

    if old:
        bpy.data.objects.remove(old, do_unlink=True)

    rows = max(1, int(rows))
    cols = max(1, int(cols))

    verts = []
    faces = []

    for y in range(rows + 1):

        fy = y / rows
        yy = (fy - 0.5) * height

        for x in range(cols + 1):

            fx = x / cols
            xx = (
                (fx - 0.5) * width
                + x_offset
            )

            verts.append((xx, yy, 0.0))

    stride = cols + 1

    for y in range(rows):

        for x in range(cols):

            a = y * stride + x
            b = a + 1
            d = (y + 1) * stride + x
            c = d + 1

            faces.append((a, b, c, d))

    mesh = bpy.data.meshes.new(name + "_MESH")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)

    obj.matrix_world = transform

    return obj


# ============================================================================
# Projection material
# ============================================================================

def create_projection_material():
    """
    Emission material that displays the master render through the
    baked CA_PROJECTED UV layer.  Existing node trees are left
    untouched so the texture never reverts to an older render when
    the rig is rebuilt.
    """

    mat = bpy.data.materials.get(MAT_PROJECTION)

    if mat is None:
        mat = bpy.data.materials.new(MAT_PROJECTION)

    mat.use_nodes = True

    nt = mat.node_tree

    if nt.nodes.get("CA_MASTER_TEX") is not None:
        return mat

    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (100.0, 0.0)

    em = nt.nodes.new("ShaderNodeEmission")
    em.location = (-150.0, 0.0)

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.name = "CA_MASTER_TEX"
    tex.interpolation = 'Linear'
    tex.extension = 'CLIP'
    tex.location = (-450.0, 0.0)

    uvm = nt.nodes.new("ShaderNodeUVMap")
    uvm.uv_map = UV_PROJECTED
    uvm.location = (-700.0, 0.0)

    image = bpy.data.images.get(IMG_MASTER)

    if image:
        tex.image = image

    nt.links.new(uvm.outputs["UV"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], em.inputs["Color"])

    if "Strength" in em.inputs:
        em.inputs["Strength"].default_value = 1.0

    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])

    return mat


def get_master_texture_node():
    mat = bpy.data.materials.get(MAT_PROJECTION)

    if mat and mat.use_nodes:
        return mat.node_tree.nodes.get("CA_MASTER_TEX")

    return None


def ensure_screen_material(obj):
    mat = create_projection_material()

    obj.data.materials.clear()
    obj.data.materials.append(mat)


# ============================================================================
# Master camera fit
# ============================================================================

def camera_focus_target(settings):
    """
    Focus point for the master camera: the center of the corner
    edge where the screens meet, plus the user's vertical offset.
    """

    return Vector((
        0.0,
        0.0,
        settings.screen_z
        + max(
            settings.left_height,
            settings.right_height
        ) * 0.5
        + settings.focus_offset
    ))


def camera_plan_azimuth(settings):
    """
    Plan-view azimuth of the master camera along its arc around the
    corner.  The viewer angle maps onto the corner angle: 0 degrees
    aligns the camera with the RIGHT screen's plane, 90 degrees with
    the LEFT screen's plane, and 45 degrees (the default) centers it
    on the exterior angle bisector.
    """

    angle = max(
        math.radians(1.0),
        min(
            math.radians(179.0),
            settings.corner_angle
        )
    )

    return angle * (
        settings.viewer_angle / math.radians(90.0)
    )


def place_camera(cam, settings):
    """
    Put the master camera on its arc at the configured distance and
    eye height, aimed at the screens' focus point.
    """

    azimuth = camera_plan_azimuth(settings)
    distance = max(0.001, settings.viewer_distance)

    cam.location = (
        -math.cos(azimuth) * distance,
        -math.sin(azimuth) * distance,
        settings.eye_height
    )

    look_at(cam, camera_focus_target(settings))


def configure_camera(cam, settings):
    """
    Configure the master camera.  The FOV is entirely
    user-controlled - nothing in the add-on overwrites it.
    """

    cam.data.type = 'PERSP'
    cam.data.lens_unit = 'FOV'
    cam.data.angle = math.radians(settings.fov)
    cam.data.shift_x = 0.0
    cam.data.shift_y = 0.0
    cam.data.clip_start = 0.01
    cam.data.clip_end = 1000.0
    cam.data.sensor_width = 36.0
    cam.data.sensor_height = 24.0
    cam.data.sensor_fit = 'HORIZONTAL'


# ============================================================================
# Screen UVs and UVProject modifier
# ============================================================================

def setup_screen_uv(screen):
    """
    Give the screen a clean 0-1 planar unwrap in the UV layer the
    UVProject modifier will overwrite.
    """

    mesh = screen.data

    uv = (
        mesh.uv_layers.get(UV_PROJECTED)
        or mesh.uv_layers.new(name=UV_PROJECTED)
    )

    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]

    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)

    span_x = max(1e-8, x1 - x0)
    span_y = max(1e-8, y1 - y0)

    for poly in mesh.polygons:

        for li in poly.loop_indices:

            co = mesh.vertices[
                mesh.loops[li].vertex_index
            ].co

            uv.data[li].uv = (
                (co.x - x0) / span_x,
                (co.y - y0) / span_y
            )

    mesh.uv_layers.active = uv
    uv.active_render = True


def setup_uv_project(screen, cam, width, height):
    """
    Tie the screen's UV layer to a UVProject modifier driven by the
    master camera, so the projection always follows the camera live.

    The projector aspect has to match the aspect of the screen
    itself, otherwise the projection samples outside the master
    image and the screens render black.
    """

    mod = screen.modifiers.get("CA_UVPROJECT")

    if mod is not None and mod.type != 'UV_PROJECT':
        screen.modifiers.remove(mod)
        mod = None

    if mod is None:
        mod = screen.modifiers.new(
            "CA_UVPROJECT",
            'UV_PROJECT'
        )

    mod.uv_layer = UV_PROJECTED
    mod.aspect_x = width / max(0.001, height)
    mod.aspect_y = 1.0
    mod.projector_count = 1
    mod.projectors[0].object = cam


# ============================================================================
# Rig creation
# ============================================================================

def update_rig(scene):

    s = scene.corner_anamorphic
    coll = get_collection(scene)

    # ------------------------------------------------------------------------
    # Physical geometry
    # ------------------------------------------------------------------------

    # corner_angle is stored in radians because it is a Blender ANGLE
    # property.
    angle = max(
        math.radians(1.0),
        min(
            math.radians(179.0),
            s.corner_angle
        )
    )

    # Plan view (looking down -Z):
    #
    #   +Y
    #   |
    #   |   LEFT screen (rotated by corner_angle around Z)
    #   |
    #   ●─────────── +X
    #   CORNER      RIGHT screen
    #
    # The viewer stands on the OUTSIDE (convex side) of the corner,
    # in the -X/-Y quadrant, on the exterior angle bisector.
    rx = mathutils.Matrix.Rotation(
        math.radians(90.0),
        4,
        'X'
    )

    rz_right = mathutils.Matrix.Identity(4)

    rz_left = mathutils.Matrix.Rotation(
        angle,
        4,
        'Z'
    )

    z_offset_right = (
        s.screen_z
        + s.right_height / 2.0
    )

    z_offset_left = (
        s.screen_z
        + s.left_height / 2.0
    )

    right_m = (
        mathutils.Matrix.Translation(
            (0.0, 0.0, z_offset_right)
        )
        @ rz_right
        @ rx
    )

    left_m = (
        mathutils.Matrix.Translation(
            (0.0, 0.0, z_offset_left)
        )
        @ rz_left
        @ rx
    )

    right = make_grid(
        OBJ_RIGHT,
        s.right_width,
        s.right_height,
        s.screen_subdivisions,
        s.screen_subdivisions,
        right_m,
        coll,
        x_offset=s.right_width / 2.0
    )

    left = make_grid(
        OBJ_LEFT,
        s.left_width,
        s.left_height,
        s.screen_subdivisions,
        s.screen_subdivisions,
        left_m,
        coll,
        x_offset=s.left_width / 2.0
    )

    ensure_screen_material(left)
    ensure_screen_material(right)

    # The screens never render in the master view (they would block
    # the scene).  The output operators unhide them temporarily when
    # capturing each side.
    left.hide_render = True
    right.hide_render = True

    # ------------------------------------------------------------------------
    # Master viewer camera
    # ------------------------------------------------------------------------

    cam = ensure_camera(OBJ_MASTER, coll)

    # The camera always points at the screens: a focus point on the
    # corner edge where they meet (bottom / center / top).
    place_camera(cam, s)

    configure_camera(cam, s)

    update_scene_graph(scene)

    # ------------------------------------------------------------------------
    # Screen UVs and camera-driven projection
    # ------------------------------------------------------------------------

    setup_screen_uv(left)
    setup_uv_project(
        left,
        cam,
        s.left_width,
        s.left_height
    )

    setup_screen_uv(right)
    setup_uv_project(
        right,
        cam,
        s.right_width,
        s.right_height
    )

    # Refresh the texture reference in case the master image exists.
    tex = get_master_texture_node()

    if tex and tex.image is None:
        tex.image = bpy.data.images.get(IMG_MASTER)

    # ------------------------------------------------------------------------
    # Scene settings
    # ------------------------------------------------------------------------

    s.master_camera_name = cam.name
    s.left_screen_name = left.name
    s.right_screen_name = right.name

    scene.camera = cam

    scene.render.resolution_x = (
        s.master_resolution_x
    )
    scene.render.resolution_y = (
        s.master_resolution_y
    )
    scene.render.resolution_percentage = (
        s.render_percentage
    )
    scene.render.fps = s.fps
    scene.render.film_transparent = (
        s.transparent_film
    )

    return cam, left, right


# ============================================================================
# Output scenes (orthographic capture of each screen)
# ============================================================================

def build_output_scene(source_scene, side):
    """
    Create a fresh output scene containing only one screen and an
    orthographic camera aimed straight at it from the outside - the
    same capture as the manual two-camera setup.
    """

    s = source_scene.corner_anamorphic

    name = (
        SCENE_OUT_LEFT
        if side == "LEFT"
        else SCENE_OUT_RIGHT
    )

    old = bpy.data.scenes.get(name)

    if old is not None:
        bpy.data.scenes.remove(old)

    out = bpy.data.scenes.new(name)

    screen = bpy.data.objects[
        s.left_screen_name
        if side == "LEFT"
        else s.right_screen_name
    ]

    if side == "LEFT":
        width = s.left_width
        height = s.left_height
    else:
        width = s.right_width
        height = s.right_height

    out.collection.objects.link(screen)

    # Orthographic camera, straight-on from the viewer side.
    cam_data = bpy.data.cameras.new(name + "_DATA")
    cam_data.type = 'ORTHO'

    cam = bpy.data.objects.new(
        "CA_OUTPUT_CAMERA_" + side,
        cam_data
    )

    out.collection.objects.link(cam)

    # The screen mesh starts at the corner edge (local X runs from
    # 0 to the screen width), so the object origin is NOT the
    # screen center - take the center of the mesh bounds instead.
    xs = [c[0] for c in screen.bound_box]
    ys = [c[1] for c in screen.bound_box]

    center = screen.matrix_world @ Vector((
        (min(xs) + max(xs)) * 0.5,
        (min(ys) + max(ys)) * 0.5,
        0.0
    ))

    normal = (
        screen.matrix_world.to_3x3()
        @ Vector((0.0, 0.0, 1.0))
    ).normalized()

    master_cam = bpy.data.objects[s.master_camera_name]

    if normal.dot(master_cam.location - center) < 0.0:
        normal = -normal

    cam.location = (
        center
        + normal * max(width, height) * 2.0
    )

    # Aim the camera straight at the screen with the capture's "up"
    # matching world up, so it matches the physical display as the
    # outside viewer sees it.  look_at() cannot be used here: the
    # view direction is horizontal, parallel to its up hint.
    x_axis = Vector((0.0, 0.0, 1.0)).cross(normal).normalized()
    y_axis = normal.cross(x_axis)

    cam.matrix_world = mathutils.Matrix((
        (x_axis.x, y_axis.x, normal.x, cam.location.x),
        (x_axis.y, y_axis.y, normal.y, cam.location.y),
        (x_axis.z, y_axis.z, normal.z, cam.location.z),
        (0.0, 0.0, 0.0, 1.0)
    ))

    # Render aspect always matches the screen aspect (resolutions are
    # tied to the screen size), so the larger dimension is what the
    # ortho scale has to cover.
    cam_data.ortho_scale = max(width, height)

    out.camera = cam

    out.render.resolution_x = max(
        4,
        int(round(
            width
            * s.resolution_multiplier
        ))
    )
    out.render.resolution_y = max(
        4,
        int(round(
            height
            * s.resolution_multiplier
        ))
    )
    out.render.resolution_percentage = (
        s.render_percentage
    )
    out.render.fps = s.fps
    out.render.film_transparent = (
        s.transparent_film
    )

    out.frame_start = source_scene.frame_start
    out.frame_end = source_scene.frame_end

    return out


def screen_viewer_corners_world(screen, cam):
    """
    The screen's four corners in world space, ordered as the
    outside viewer sees the display: bottom-left, bottom-right,
    top-right, top-left.
    """

    xs = [c[0] for c in screen.bound_box]
    ys = [c[1] for c in screen.bound_box]

    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)

    ordered_local = [
        (x0, y0, 0.0),
        (x1, y0, 0.0),
        (x1, y1, 0.0),
        (x0, y1, 0.0)
    ]

    # Flip horizontally when the screen's local +X points to the
    # outside viewer's left, so the capture matches the physical
    # display orientation.
    normal = (
        screen.matrix_world.to_3x3()
        @ Vector((0.0, 0.0, 1.0))
    )

    to_cam = (
        cam.location
        - screen.matrix_world.translation
    )

    if normal.dot(to_cam) < 0.0:
        normal = -normal

    viewer_right = (-normal).cross(
        Vector((0.0, 0.0, 1.0))
    )

    local_x = (
        screen.matrix_world.to_3x3()
        @ Vector((1.0, 0.0, 0.0))
    )

    if local_x.dot(viewer_right) < 0.0:
        ordered_local = [
            ordered_local[1],
            ordered_local[0],
            ordered_local[3],
            ordered_local[2]
        ]

    return [
        screen.matrix_world @ Vector(corner)
        for corner in ordered_local
    ]


def build_combined_capture(source_scene):
    """
    Create the combined capture mesh: two subdivided grids side by
    side (LEFT on the left, RIGHT on the right).  Every vertex gets
    its UV from projecting the matching point of the real screen
    into the master camera frame, so one straight-on orthographic
    render produces the seamless wide frame.

    The subdivision matters: the camera-to-screen mapping is
    projective, so corner-only UVs on plain quads stretch the
    image.  Interpolating per vertex approximates it closely.
    """

    s = source_scene.corner_anamorphic

    cam = bpy.data.objects[s.master_camera_name]

    update_scene_graph(source_scene)

    old = bpy.data.objects.get(OBJ_COMBINED)

    if old:
        bpy.data.objects.remove(old, do_unlink=True)

    W_left = s.left_width
    H_left = s.left_height
    W_right = s.right_width
    H_right = s.right_height
    n = max(1, int(s.screen_subdivisions))

    verts = []
    faces = []
    vert_uvs = []

    for side in ("LEFT", "RIGHT"):

        if side == "LEFT":
            W = W_left
            H = H_left
        else:
            W = W_right
            H = H_right

        screen = bpy.data.objects[
            s.left_screen_name
            if side == "LEFT"
            else s.right_screen_name
        ]

        bl, br, tr, tl = screen_viewer_corners_world(
            screen,
            cam
        )

        base = len(verts)
        stride = n + 1

        for j in range(n + 1):

            v = j / n
            y = v * H

            for i in range(n + 1):

                u = i / n

                if side == "LEFT":
                    x = (u - 1.0) * W_left
                else:
                    x = u * W_right

                # The screens are planar rectangles, so bilinear
                # interpolation between their corners lands exactly
                # on the matching surface point.
                world = (
                    bl * ((1.0 - u) * (1.0 - v))
                    + br * (u * (1.0 - v))
                    + tr * (u * v)
                    + tl * ((1.0 - u) * v)
                )

                q = world_to_camera_view(
                    source_scene,
                    cam,
                    world
                )

                verts.append((x, y, 0.0))
                vert_uvs.append((q.x, q.y))

        for j in range(n):

            for i in range(n):

                a = base + j * stride + i
                b = a + 1
                d = a + stride
                c = d + 1

                faces.append((a, b, c, d))

    mesh = bpy.data.meshes.new(OBJ_COMBINED + "_MESH")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    uv = mesh.uv_layers.new(name=UV_PROJECTED)

    for poly in mesh.polygons:

        for li in poly.loop_indices:

            vi = mesh.loops[li].vertex_index
            uv.data[li].uv = vert_uvs[vi]

    mesh.uv_layers.active = uv
    uv.active_render = True

    obj = bpy.data.objects.new(OBJ_COMBINED, mesh)

    ensure_screen_material(obj)

    return obj


def build_combined_scene(source_scene):
    """
    Create a fresh output scene containing only the combined
    capture mesh and an orthographic camera aimed straight at it.
    """

    s = source_scene.corner_anamorphic

    old = bpy.data.scenes.get(SCENE_OUT_COMBINED)

    if old is not None:
        bpy.data.scenes.remove(old)

    out = bpy.data.scenes.new(SCENE_OUT_COMBINED)

    capture = build_combined_capture(source_scene)

    out.collection.objects.link(capture)

    capture.hide_render = False

    cam_data = bpy.data.cameras.new(
        SCENE_OUT_COMBINED + "_DATA"
    )
    cam_data.type = 'ORTHO'

    cam = bpy.data.objects.new(
        "CA_OUTPUT_CAMERA_COMBINED",
        cam_data
    )

    out.collection.objects.link(cam)

    W_total = s.left_width + s.right_width
    H = max(s.left_height, s.right_height)

    center = Vector((
        (s.right_width - s.left_width) * 0.5,
        H * 0.5,
        0.0
    ))

    cam.location = center + Vector((
        0.0,
        0.0,
        max(W_total, H) * 2.0
    ))

    look_at(cam, center)

    cam_data.ortho_scale = max(W_total, H)

    out.camera = cam

    out.render.resolution_x = max(
        4,
        int(round(
            W_total
            * s.resolution_multiplier
        ))
    )
    out.render.resolution_y = max(
        4,
        int(round(
            H
            * s.resolution_multiplier
        ))
    )
    out.render.resolution_percentage = (
        s.render_percentage
    )
    out.render.fps = s.fps
    out.render.film_transparent = (
        s.transparent_film
    )

    out.frame_start = source_scene.frame_start
    out.frame_end = source_scene.frame_end

    return out


def resolve_output_dir(s):
    """
    Absolute output directory for the current settings.

    '//' paths resolve against the .blend file; when the file has
    never been saved Blender silently falls back to the system temp
    folder, which is never where these renders should go.
    """

    if (
        s.output_directory.startswith("//")
        and not bpy.data.filepath
    ):
        raise RuntimeError(
            "Save the .blend file first - the output "
            "directory is relative to the project file."
        )

    return bpy.path.abspath(s.output_directory)


def latest_feed_output(s):
    """
    Directory of the most recently written screen-feed render
    (a *_frames directory or the output directory containing a
    feed video), or None when no feeds have been rendered yet.
    """

    try:
        output_dir = resolve_output_dir(s)
    except RuntimeError:
        return None

    if not os.path.isdir(output_dir):
        return None

    newest_dir = None
    newest_mtime = -1.0

    def consider(path, is_dir):
        nonlocal newest_dir, newest_mtime

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return

        if mtime > newest_mtime:
            newest_mtime = mtime
            newest_dir = (
                path if is_dir else output_dir
            )

    for base in (
        s.left_filename,
        s.right_filename,
        s.combined_filename
    ):

        base = os.path.splitext(base)[0]

        frames_dir = os.path.join(
            output_dir,
            base + "_frames"
        )

        if (
            os.path.isdir(frames_dir)
            and os.listdir(frames_dir)
        ):
            consider(frames_dir, True)

        for name in os.listdir(output_dir):

            if (
                name.startswith(base)
                and name.lower().endswith(
                    VIDEO_FILE_EXTENSIONS
                )
            ):
                consider(
                    os.path.join(output_dir, name),
                    False
                )

    return newest_dir


VIDEO_CODECS = {
    'MPEG4': 'H264',
    'MKV': 'H264',
    'QUICKTIME': 'H264',
    'AVI': 'H264',
    'WEBM': 'WEBM',
    'MPEG1': 'MPEG1',
    'MPEG2': 'MPEG2',
    'OGG': 'THEORA',
    'DV': 'DV'
}

VIDEO_FILE_EXTENSIONS = (
    ".mp4", ".mkv", ".mov", ".avi", ".webm",
    ".ogv", ".mpg", ".mpeg", ".dv"
)


def configure_video_format(render, video_format):
    """
    Point a render at video output in the given container,
    picking a codec the container actually supports.
    Blender 5.x gates video formats behind the media type;
    on 4.x file_format accepts 'FFMPEG' directly.
    """

    image_settings = render.image_settings

    if hasattr(image_settings, "media_type"):
        image_settings.media_type = 'VIDEO'

    image_settings.file_format = 'FFMPEG'
    render.ffmpeg.format = video_format
    render.ffmpeg.codec = VIDEO_CODECS.get(
        video_format,
        'H264'
    )


def configure_output_format(out, s, filename):
    """
    Set the output scene up for either MP4 or an image sequence, and
    pick the output filepath.
    """

    output_dir = resolve_output_dir(s)

    os.makedirs(output_dir, exist_ok=True)

    if s.output_mode == 'MP4':

        try:
            configure_video_format(
                out.render,
                s.video_format
            )

        except (TypeError, AttributeError):
            raise RuntimeError(
                "Video output is not available in this "
                "Blender build.  Switch the output mode "
                "to Image Sequence instead."
            )

        out.render.filepath = os.path.join(
            output_dir,
            os.path.splitext(filename)[0]
        )

    else:

        image_settings = out.render.image_settings

        if hasattr(image_settings, "media_type"):
            image_settings.media_type = 'IMAGE'

        image_settings.file_format = (
            s.image_format
        )

        base = os.path.splitext(filename)[0]

        frames_dir = os.path.join(
            output_dir,
            base + "_frames"
        )

        os.makedirs(frames_dir, exist_ok=True)

        out.render.filepath = os.path.join(
            frames_dir,
            base + "_"
        )


# ============================================================================
# Master rendering
# ============================================================================

def apply_master_crop(scene):
    """
    Restrict the master render to the region of the frame the
    screens actually cover, using Blender's render border.

    Crop-to-border stays OFF: the output image keeps the full
    master resolution (the area outside the border is simply not
    rendered), so the projection UVs still line up with the
    uncropped frame.  Only the pixels inside the border cost
    render time.

    Returns the previous border settings so they can be restored
    (via restore_master_crop) once the render has ended.
    """

    render = scene.render

    saved = {
        "use_border": render.use_border,
        "use_crop_to_border": render.use_crop_to_border,
        "border_min_x": render.border_min_x,
        "border_max_x": render.border_max_x,
        "border_min_y": render.border_min_y,
        "border_max_y": render.border_max_y
    }

    s = scene.corner_anamorphic

    if not s.crop_master:
        return saved

    cam = bpy.data.objects.get(s.master_camera_name)

    if cam is None:
        return saved

    update_scene_graph(scene)

    xs = []
    ys = []

    for name in (s.left_screen_name, s.right_screen_name):

        screen = bpy.data.objects.get(name)

        if screen is None:
            continue

        for corner in screen.bound_box:

            world = screen.matrix_world @ Vector(corner)

            q = world_to_camera_view(scene, cam, world)

            xs.append(q.x)
            ys.append(q.y)

    if not xs:
        return saved

    # The border is a fraction of the frame; convert the pixel
    # margin against the actual render size.
    scale = render.resolution_percentage / 100.0
    res_x = max(1, int(render.resolution_x * scale))
    res_y = max(1, int(render.resolution_y * scale))

    margin_x = s.crop_margin / res_x
    margin_y = s.crop_margin / res_y

    min_x = max(0.0, min(xs) - margin_x)
    max_x = min(1.0, max(xs) + margin_x)
    min_y = max(0.0, min(ys) - margin_y)
    max_y = min(1.0, max(ys) + margin_y)

    # Screens entirely outside the camera frame - cropping to an
    # empty border would break the render, so leave it alone.
    if min_x >= max_x or min_y >= max_y:
        return saved

    render.use_border = True
    render.use_crop_to_border = False
    render.border_min_x = min_x
    render.border_max_x = max_x
    render.border_min_y = min_y
    render.border_max_y = max_y

    return saved


def restore_master_crop(scene, saved):

    render = scene.render

    render.use_border = saved["use_border"]
    render.use_crop_to_border = saved["use_crop_to_border"]
    render.border_min_x = saved["border_min_x"]
    render.border_max_x = saved["border_max_x"]
    render.border_min_y = saved["border_min_y"]
    render.border_max_y = saved["border_max_y"]


def set_still_image_format(scene):
    """
    Point the scene's output at a still PNG.  Blender 5.x splits
    image and video output behind image_settings.media_type, so a
    scene left on video output rejects image formats outright.
    """

    image_settings = scene.render.image_settings

    if hasattr(image_settings, "media_type"):
        image_settings.media_type = 'IMAGE'

    image_settings.file_format = 'PNG'


def render_master_still(scene, filepath):
    """
    Start the render of one master frame with the screens hidden
    (non-blocking - the crop border is restored by the render
    complete/cancel handlers).
    """

    s = scene.corner_anamorphic

    cam = bpy.data.objects[s.master_camera_name]
    left = bpy.data.objects[s.left_screen_name]
    right = bpy.data.objects[s.right_screen_name]

    left.hide_render = True
    right.hide_render = True

    set_still_image_format(scene)
    scene.render.filepath = filepath
    scene.camera = cam

    saved = apply_master_crop(scene)

    start_render(
        after_render=lambda: restore_master_crop(scene, saved),
        write_still=True
    )


def refresh_screen_previews():
    """
    Force viewports in Material Preview / Rendered shading to pick
    up a newly loaded master image.  Without the update tags the
    GPU texture cache keeps showing the previous master until the
    shading mode is switched away and back.
    """

    mat = bpy.data.materials.get(MAT_PROJECTION)

    if mat is not None:
        mat.update_tag()

    for name in (OBJ_LEFT, OBJ_RIGHT, OBJ_COMBINED):
        obj = bpy.data.objects.get(name)

        if obj is not None:
            obj.update_tag()

    wm = bpy.context.window_manager

    if wm is None:
        return

    for window in wm.windows:

        screen = window.screen

        if screen is None:
            continue

        for area in screen.areas:

            if area.type == 'VIEW_3D':
                area.tag_redraw()


def load_master_image(filepath):
    """
    Point the projection material at the given master render.  The
    image datablock is replaced outright so a re-render can never
    leave the screens showing a cached copy of the previous master.
    """

    if not os.path.exists(filepath):
        raise RuntimeError(
            f"Master render was not written to '{filepath}'."
        )

    old = bpy.data.images.get(IMG_MASTER)

    if old:
        bpy.data.images.remove(old)

    img = bpy.data.images.load(
        filepath,
        check_existing=False
    )
    img.name = IMG_MASTER

    tex = get_master_texture_node()

    if tex is not None:
        tex.image = img

    refresh_screen_previews()

    return img


def load_master_sequence(frames_dir, first, last):
    """
    Point the projection material at the rendered master frame
    sequence.  Sequence playback settings live on the texture
    node's image user, not on the image datablock.
    """

    first_path = os.path.join(
        frames_dir,
        f"master_{first:04d}.png"
    )

    if not os.path.exists(first_path):
        raise RuntimeError(
            "Master frames were not written to "
            f"'{frames_dir}'."
        )

    old = bpy.data.images.get(IMG_MASTER_SEQ)

    if old:
        bpy.data.images.remove(old)

    seq = bpy.data.images.load(
        first_path,
        check_existing=False
    )
    seq.name = IMG_MASTER_SEQ
    seq.source = 'SEQUENCE'

    tex = get_master_texture_node()

    if tex is not None:
        tex.image = seq

        user = tex.image_user
        user.frame_start = first
        user.frame_duration = last - first + 1
        user.frame_offset = 0
        user.use_cyclic = False
        user.use_auto_refresh = True

    refresh_screen_previews()

    return seq


def load_master_video(video_dir, first, last):
    """
    Point the projection material at the rendered master video.
    Playback settings live on the texture node's image user, the
    same as for image sequences.
    """

    video_path = None

    if os.path.isdir(video_dir):

        for name in sorted(os.listdir(video_dir)):

            if name.lower().endswith(VIDEO_FILE_EXTENSIONS):
                video_path = os.path.join(video_dir, name)
                break

    if video_path is None:
        raise RuntimeError(
            "Master video was not written to "
            f"'{video_dir}'."
        )

    old = bpy.data.images.get(IMG_MASTER_SEQ)

    if old:
        bpy.data.images.remove(old)

    movie = bpy.data.images.load(
        video_path,
        check_existing=False
    )
    movie.name = IMG_MASTER_SEQ
    movie.source = 'MOVIE'

    tex = get_master_texture_node()

    if tex is not None:
        tex.image = movie

        user = tex.image_user
        user.frame_start = first
        user.frame_duration = last - first + 1
        user.frame_offset = 0
        user.use_cyclic = False
        user.use_auto_refresh = True

    refresh_screen_previews()

    return movie


# ============================================================================
# Operators
# ============================================================================

class CA_OT_build_rig(bpy.types.Operator):

    bl_idname = "corner_anamorphic.build_rig"
    bl_label = "Build / Rebuild Rig"

    bl_description = (
        "Create or rebuild the viewer camera, "
        "screens and projection data"
    )

    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        try:

            cam, left, right = update_rig(
                context.scene
            )

            context.scene.camera = cam

            set_view_camera(context, cam)

            self.report(
                {'INFO'},
                "Corner anamorphic rig built."
            )

            return {'FINISHED'}

        except Exception as exc:

            self.report(
                {'ERROR'},
                f"Rig build failed: {exc}"
            )

            return {'CANCELLED'}


class CA_OT_ffmpeg_warning(bpy.types.Operator):

    bl_idname = "corner_anamorphic.ffmpeg_warning"
    bl_label = "Corner Anamorphic: MP4 Unavailable"

    bl_options = {'REGISTER', 'INTERNAL'}

    def invoke(self, context, event):

        return context.window_manager.invoke_props_dialog(
            self,
            width=430
        )

    def execute(self, context):

        return {'FINISHED'}

    def draw(self, context):

        col = self.layout.column(align=True)
        col.label(
            text=(
                "This looks like the Microsoft Store "
                "version of Blender, which ships"
            )
        )
        col.label(
            text="without video (FFmpeg) support."
        )

        col.separator()

        col.label(
            text=(
                "MP4 output will not work - use "
                "Image Sequence mode instead, or"
            )
        )
        col.label(
            text=(
                "install Blender from blender.org "
                "to enable MP4."
            )
        )


class CA_OT_change_output_directory(bpy.types.Operator):

    bl_idname = "corner_anamorphic.change_output_directory"
    bl_label = "Change Directory..."

    bl_description = (
        "Choose where the renders are written "
        "(defaults to //corner_anamorphic/ next to "
        "the .blend file)"
    )

    bl_options = {'REGISTER'}

    directory: bpy.props.StringProperty(
        subtype='DIR_PATH'
    )

    filter_folder: bpy.props.BoolProperty(
        default=True,
        options={'HIDDEN'}
    )

    def invoke(self, context, event):

        try:
            self.directory = bpy.path.abspath(
                context.scene.corner_anamorphic
                .output_directory
            )
        except Exception:
            pass

        context.window_manager.fileselect_add(self)

        return {'RUNNING_MODAL'}

    def execute(self, context):

        context.scene.corner_anamorphic.output_directory = (
            self.directory
        )

        return {'FINISHED'}


class CA_OT_open_output_directory(bpy.types.Operator):

    bl_idname = "corner_anamorphic.open_output_directory"
    bl_label = "Open Output Directory"

    bl_description = (
        "Open the output directory in the "
        "system file browser"
    )

    bl_options = {'REGISTER'}

    def execute(self, context):

        s = context.scene.corner_anamorphic

        try:
            output_dir = resolve_output_dir(s)

        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        os.makedirs(output_dir, exist_ok=True)

        bpy.ops.wm.path_open(filepath=output_dir)

        return {'FINISHED'}


class CA_OT_open_render_directory(bpy.types.Operator):

    bl_idname = "corner_anamorphic.open_render_directory"
    bl_label = "Open Render Directory"

    bl_description = (
        "Open the directory of the most recent "
        "screen animation render in the system "
        "file browser"
    )

    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):

        scene = context.scene

        return (
            scene is not None
            and latest_feed_output(
                scene.corner_anamorphic
            ) is not None
        )

    def execute(self, context):

        target = latest_feed_output(
            context.scene.corner_anamorphic
        )

        if target is None:

            self.report(
                {'WARNING'},
                "No screen animation renders found."
            )

            return {'CANCELLED'}

        bpy.ops.wm.path_open(filepath=target)

        return {'FINISHED'}


class CA_OT_delete_rig(bpy.types.Operator):

    bl_idname = "corner_anamorphic.delete_rig"
    bl_label = "Delete Rig"

    bl_description = (
        "Delete the objects and output scenes "
        "created by this add-on"
    )

    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        for name in (
            OBJ_MASTER,
            OBJ_LEFT,
            OBJ_RIGHT,
            OBJ_COMBINED,
            "CA_OUTPUT_CAMERA_LEFT",
            "CA_OUTPUT_CAMERA_RIGHT",
            "CA_OUTPUT_CAMERA_COMBINED"
        ):

            remove_object(name)

        coll = bpy.data.collections.get(COLL_NAME)

        if coll and len(coll.objects) == 0:

            for scene in bpy.data.scenes:

                if coll.name in scene.collection.children:

                    try:
                        scene.collection.children.unlink(
                            coll
                        )
                    except RuntimeError:
                        pass

            bpy.data.collections.remove(coll)

        for scene_name in (
            SCENE_OUT_LEFT,
            SCENE_OUT_RIGHT,
            SCENE_OUT_COMBINED
        ):

            out = bpy.data.scenes.get(scene_name)

            if out:
                bpy.data.scenes.remove(out)

        self.report(
            {'INFO'},
            "Corner anamorphic rig deleted."
        )

        return {'FINISHED'}


class CA_OT_frame_master(bpy.types.Operator):

    bl_idname = "corner_anamorphic.frame_master"
    bl_label = "View Master"

    bl_description = (
        "Make the master viewer camera active"
    )

    bl_options = {'REGISTER'}

    def execute(self, context):

        cam = bpy.data.objects.get(
            context.scene.corner_anamorphic.master_camera_name
        )

        if not cam:

            self.report(
                {'WARNING'},
                "Build the rig first."
            )

            return {'CANCELLED'}

        context.scene.camera = cam

        set_view_camera(context, cam)

        return {'FINISHED'}


# ============================================================================
# Render operators
# ============================================================================

class CA_OT_render_master(bpy.types.Operator):

    bl_idname = "corner_anamorphic.render_master"
    bl_label = "Render Screen Preview"

    bl_description = (
        "Render the master viewer camera for the "
        "current frame and preview it on the screens"
    )

    bl_options = {'REGISTER'}

    def execute(self, context):

        if job_running():

            self.report(
                {'WARNING'},
                "A render job is already running."
            )

            return {'CANCELLED'}

        scene = context.scene
        s = scene.corner_anamorphic

        try:

            update_rig(scene)

            output_dir = resolve_output_dir(s)

            os.makedirs(output_dir, exist_ok=True)

            path = os.path.join(
                output_dir,
                "CA_MASTER_FRAME.png"
            )

        except Exception as exc:

            self.report(
                {'ERROR'},
                f"Master render failed: {exc}"
            )

            return {'CANCELLED'}

        def step_render():
            progress_begin("Master frame", 1)
            render_master_still(scene, path)

        def step_load():
            load_master_image(path)
            progress_finish(
                f"Master frame rendered: {path}"
            )

        start_job([step_render, step_load])

        self.report(
            {'INFO'},
            "Rendering master frame..."
        )

        return {'FINISHED'}


def master_sequence_present(output_dir, first, last):

    first_path = os.path.join(
        output_dir,
        "master_frames",
        f"master_{first:04d}.png"
    )
    last_path = os.path.join(
        output_dir,
        "master_frames",
        f"master_{last:04d}.png"
    )

    return (
        os.path.exists(first_path)
        and os.path.exists(last_path)
    )


def master_video_present(output_dir):

    video_dir = os.path.join(
        output_dir,
        "master_video"
    )

    return os.path.isdir(video_dir) and any(
        name.lower().endswith(VIDEO_FILE_EXTENSIONS)
        for name in os.listdir(video_dir)
    )


def master_animation_present(scene):
    """
    True when a master animation exists on disk - either the
    image sequence (first and last frame of the scene's current
    frame range are checked) or the video, so the screen feeds
    can be rendered in either format regardless of how the
    master was rendered.
    """

    s = scene.corner_anamorphic

    try:
        output_dir = resolve_output_dir(s)
    except RuntimeError:
        return False

    return (
        master_sequence_present(
            output_dir,
            scene.frame_start,
            scene.frame_end
        )
        or master_video_present(output_dir)
    )


def render_master_sequence(source, output_dir):
    """
    Start rendering the master camera as PNG frames into
    <output>/master_frames/ (non-blocking - the crop border is
    restored by the render complete/cancel handlers).
    """

    frames_dir = os.path.join(
        output_dir,
        "master_frames"
    )

    os.makedirs(frames_dir, exist_ok=True)

    set_still_image_format(source)

    source.render.filepath = os.path.join(
        frames_dir,
        "master_"
    )

    saved = apply_master_crop(source)

    start_render(
        after_render=lambda: restore_master_crop(source, saved),
        animation=True
    )


def render_master_video(source, output_dir):
    """
    Start rendering the master camera as a single video file
    into <output>/master_video/ (non-blocking).  The user's own
    output settings and the crop border are restored by the
    render complete/cancel handlers.
    """

    s = source.corner_anamorphic

    render = source.render
    image_settings = render.image_settings

    saved_settings = {
        "media_type": getattr(
            image_settings,
            "media_type",
            None
        ),
        "file_format": image_settings.file_format,
        "ffmpeg_format": render.ffmpeg.format,
        "ffmpeg_codec": render.ffmpeg.codec,
        "filepath": render.filepath
    }

    def restore_output_settings():

        if saved_settings["media_type"] is not None:
            image_settings.media_type = (
                saved_settings["media_type"]
            )

        image_settings.file_format = (
            saved_settings["file_format"]
        )
        render.ffmpeg.format = (
            saved_settings["ffmpeg_format"]
        )
        render.ffmpeg.codec = (
            saved_settings["ffmpeg_codec"]
        )
        render.filepath = saved_settings["filepath"]

    video_dir = os.path.join(
        output_dir,
        "master_video"
    )

    os.makedirs(video_dir, exist_ok=True)

    # Clear previous master videos so the fresh render is the
    # only candidate when loading.
    for name in os.listdir(video_dir):
        os.remove(os.path.join(video_dir, name))

    try:
        configure_video_format(render, s.video_format)

    except (TypeError, AttributeError):
        restore_output_settings()
        raise

    render.filepath = os.path.join(
        video_dir,
        "master"
    )

    saved_crop = apply_master_crop(source)

    def after_render():
        restore_master_crop(source, saved_crop)
        restore_output_settings()

    start_render(
        after_render=after_render,
        animation=True
    )


def render_master_frames(source, window):
    """
    Start the master camera render for the scene frame range
    (non-blocking), as PNG frames or as a video file following
    the output mode.
    """

    s = source.corner_anamorphic

    output_dir = resolve_output_dir(s)

    first = source.frame_start
    last = source.frame_end

    left = bpy.data.objects[s.left_screen_name]
    right = bpy.data.objects[s.right_screen_name]

    left.hide_render = True
    right.hide_render = True

    source.camera = bpy.data.objects[
        s.master_camera_name
    ]

    window.scene = source

    progress_begin("Master frames", last - first + 1)

    if s.output_mode == 'MP4':
        render_master_video(source, output_dir)
    else:
        render_master_sequence(source, output_dir)


def load_master_animation(scene):
    """
    Feed the rendered master animation to the projection
    material, preferring the format that matches the output
    mode and falling back to whichever exists on disk - a
    master rendered as video can drive image-sequence feeds
    and the other way around.
    """

    s = scene.corner_anamorphic

    output_dir = resolve_output_dir(s)

    first = scene.frame_start
    last = scene.frame_end

    sequence_ok = master_sequence_present(
        output_dir,
        first,
        last
    )
    video_ok = master_video_present(output_dir)

    prefer_video = (
        s.output_mode == 'MP4'
        and video_ok
    )

    if prefer_video or (video_ok and not sequence_ok):

        load_master_video(
            os.path.join(output_dir, "master_video"),
            first,
            last
        )

    elif sequence_ok:

        load_master_sequence(
            os.path.join(output_dir, "master_frames"),
            first,
            last
        )

    else:
        raise RuntimeError(
            "No rendered master animation found - "
            "render the master animation first."
        )


def screen_feed_steps(source, s, window):
    """
    Build the render steps for the screen feeds (separate L/R
    or the combined wide frame).  The projection material must
    already point at the master animation.
    """

    first = source.frame_start
    last = source.frame_end

    steps = []

    if s.render_layout == 'COMBINED':

        def step_combined():
            out = build_combined_scene(source)

            configure_output_format(
                out,
                s,
                s.combined_filename
            )

            progress_begin("COMBINED", last - first + 1)

            window.scene = out

            start_render(animation=True)

        steps.append(step_combined)

    else:

        for side, filename in (
            ("LEFT", s.left_filename),
            ("RIGHT", s.right_filename)
        ):

            def make_steps(side=side, filename=filename):

                screen_name = (
                    s.left_screen_name
                    if side == "LEFT"
                    else s.right_screen_name
                )

                def step_render():
                    out = build_output_scene(source, side)

                    configure_output_format(
                        out,
                        s,
                        filename
                    )

                    progress_begin(
                        side,
                        last - first + 1
                    )

                    bpy.data.objects[
                        screen_name
                    ].hide_render = False

                    window.scene = out

                    start_render(animation=True)

                def step_hide():
                    bpy.data.objects[
                        screen_name
                    ].hide_render = True

                return step_render, step_hide

            step_render, step_hide = make_steps()

            steps.append(step_render)
            steps.append(step_hide)

    return steps


class CA_OT_render_master_animation(bpy.types.Operator):

    bl_idname = "corner_anamorphic.render_master_animation"
    bl_label = "Render Master Animation"

    bl_description = (
        "Render the master viewer camera for the scene "
        "frame range; the screens sample these frames"
    )

    bl_options = {'REGISTER'}

    def execute(self, context):

        if job_running():

            self.report(
                {'WARNING'},
                "A render job is already running."
            )

            return {'CANCELLED'}

        source = context.scene

        original_frame = source.frame_current

        try:
            update_rig(source)

            # Fail early, before the job starts.
            resolve_output_dir(source.corner_anamorphic)

        except Exception as exc:

            self.report(
                {'ERROR'},
                f"Master animation failed: {exc}"
            )

            return {'CANCELLED'}

        def step_render():
            render_master_frames(source, context.window)

        def step_load():
            load_master_animation(source)
            source.frame_set(original_frame)
            progress_finish("Master animation rendered.")

        def cleanup():
            source.frame_set(original_frame)

        start_job(
            [step_render, step_load],
            cleanup=cleanup
        )

        self.report(
            {'INFO'},
            "Rendering master animation..."
        )

        return {'FINISHED'}


class CA_OT_delete_master_animation(bpy.types.Operator):

    bl_idname = "corner_anamorphic.delete_master_animation"
    bl_label = "Delete Master Animation"

    bl_description = (
        "Delete the rendered master animation and "
        "unload it from the screens"
    )

    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):

        scene = context.scene

        return (
            scene is not None
            and master_animation_present(scene)
        )

    def execute(self, context):

        scene = context.scene
        s = scene.corner_anamorphic

        try:

            # Unload the master animation first: on Windows a
            # loaded movie texture holds the video file open,
            # which would make the deletion below fail.
            tex = get_master_texture_node()

            if tex is not None:
                tex.image = bpy.data.images.get(IMG_MASTER)

            seq = bpy.data.images.get(IMG_MASTER_SEQ)

            if seq:
                bpy.data.images.remove(seq)

            for sub_dir in (
                "master_frames",
                "master_video"
            ):

                target_dir = os.path.join(
                    resolve_output_dir(s),
                    sub_dir
                )

                if not os.path.isdir(target_dir):
                    continue

                for name in os.listdir(target_dir):
                    os.remove(
                        os.path.join(target_dir, name)
                    )

                try:
                    os.rmdir(target_dir)
                except OSError:
                    pass

            progress_finish("Master animation deleted.")

            self.report(
                {'INFO'},
                "Master animation deleted."
            )

            return {'FINISHED'}

        except Exception as exc:

            self.report(
                {'ERROR'},
                f"Delete failed: {exc}"
            )

            return {'CANCELLED'}


class CA_OT_render_screen_animation(bpy.types.Operator):

    bl_idname = "corner_anamorphic.render_screen_animation"
    bl_label = "Render Screen Animation"

    bl_description = (
        "Render the screen feeds for the scene frame "
        "range from the rendered master animation "
        "(render the master animation first)"
    )

    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):

        scene = context.scene

        return (
            scene is not None
            and master_animation_present(scene)
        )

    def execute(self, context):

        if job_running():

            self.report(
                {'WARNING'},
                "A render job is already running."
            )

            return {'CANCELLED'}

        source = context.scene
        s = source.corner_anamorphic

        window = context.window

        original_scene = window.scene
        original_frame = source.frame_current

        try:
            update_rig(source)

        except Exception as exc:

            self.report(
                {'ERROR'},
                f"Screen animation failed: {exc}"
            )

            return {'CANCELLED'}

        def step_load():
            load_master_animation(source)

        def step_finish():
            window.scene = original_scene
            source.frame_set(original_frame)
            progress_finish(
                "Screen animation render completed."
            )

        def cleanup():

            try:
                window.scene = original_scene
            except Exception:
                pass

            source.frame_set(original_frame)

        steps = [step_load]

        steps += screen_feed_steps(source, s, window)

        steps.append(step_finish)

        start_job(steps, cleanup=cleanup)

        self.report(
            {'INFO'},
            "Rendering screen animation..."
        )

        return {'FINISHED'}


# ============================================================================
# Viewport camera helper
# ============================================================================

def set_view_camera(context, cam):
    """
    Make the supplied camera the active scene camera and switch the
    first available 3D viewport into camera view.
    """

    if cam is None or cam.type != 'CAMERA':
        return

    scene = context.scene
    scene.camera = cam

    screen = context.screen

    if screen is None:
        return

    for area in screen.areas:

        if area.type != 'VIEW_3D':
            continue

        space = area.spaces.active
        region_3d = space.region_3d

        region_3d.view_perspective = 'CAMERA'
        region_3d.view_camera_zoom = 1.0
        region_3d.view_camera_offset = (0.0, 0.0)

        break


# ============================================================================
# Live setting updates
# ============================================================================
#
# Controls that do not require a rig rebuild apply their effect
# immediately to the existing rig.

def live_camera_update(self, context):
    """
    FOV, viewer angle and focus offset apply directly to the
    existing master camera; the UVProject modifiers follow the
    camera on their own.
    """

    try:

        scene = context.scene
        cam = bpy.data.objects.get(
            self.master_camera_name
        )

        if cam is None:
            return

        configure_camera(cam, self)
        place_camera(cam, self)

        update_scene_graph(scene)

    except Exception:
        pass


def live_link_width_left(self, context):
    if (
        self.link_width
        and self.right_width != self.left_width
    ):
        self.right_width = self.left_width


def live_link_width_right(self, context):
    if (
        self.link_width
        and self.left_width != self.right_width
    ):
        self.left_width = self.right_width


def live_link_height_left(self, context):
    if (
        self.link_height
        and self.right_height != self.left_height
    ):
        self.right_height = self.left_height


def live_link_height_right(self, context):
    if (
        self.link_height
        and self.left_height != self.right_height
    ):
        self.left_height = self.right_height


def live_link_width_toggled(self, context):
    if (
        self.link_width
        and self.right_width != self.left_width
    ):
        self.right_width = self.left_width


def live_link_height_toggled(self, context):
    if (
        self.link_height
        and self.right_height != self.left_height
    ):
        self.right_height = self.left_height


def live_render_update(self, context):
    """
    Render-engine-level settings apply directly to the scene.
    """

    try:

        scene = context.scene

        scene.render.resolution_x = (
            self.master_resolution_x
        )
        scene.render.resolution_y = (
            self.master_resolution_y
        )
        scene.render.resolution_percentage = (
            self.render_percentage
        )
        scene.render.fps = self.fps
        scene.render.film_transparent = (
            self.transparent_film
        )

    except Exception:
        pass


# ============================================================================
# Settings
# ============================================================================

class CA_Settings(bpy.types.PropertyGroup):

    # ------------------------------------------------------------------------
    # Viewer
    # ------------------------------------------------------------------------

    viewer_distance: bpy.props.FloatProperty(
        name="Distance",
        description="Viewer eye distance from the corner",
        default=3.0,
        min=0.1,
        max=100.0,
        unit='LENGTH'
    )

    eye_height: bpy.props.FloatProperty(
        name="Eye Height",
        description="Viewer eye height above floor",
        default=1.60,
        min=0.1,
        max=20.0,
        unit='LENGTH'
    )

    viewer_angle: bpy.props.FloatProperty(
        name="Angle",
        description=(
            "Viewer position along the arc between the "
            "two screens: 45 degrees is the centered "
            "default, 0 degrees aligns with the right "
            "screen's plane, 90 degrees with the left "
            "screen's plane"
        ),
        default=math.radians(45.0),
        min=0.0,
        max=math.radians(90.0),
        subtype='ANGLE',
        update=live_camera_update
    )

    fov: bpy.props.FloatProperty(
        name="FOV",
        description=(
            "Master viewer camera horizontal "
            "field of view in degrees - used "
            "exactly as entered, never overwritten"
        ),
        default=90.0,
        min=5.0,
        max=170.0,
        unit='NONE',
        update=live_camera_update
    )

    focus_offset: bpy.props.FloatProperty(
        name="Focus Offset",
        description=(
            "Vertical offset of the camera focus "
            "point from the center of the screens' "
            "meeting corner"
        ),
        default=0.0,
        min=-100.0,
        max=100.0,
        unit='LENGTH',
        update=live_camera_update
    )

    # ------------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------------

    left_width: bpy.props.FloatProperty(
        name="Left Width",
        default=3.0,
        min=0.1,
        max=100.0,
        unit='LENGTH',
        update=live_link_width_left
    )

    right_width: bpy.props.FloatProperty(
        name="Right Width",
        default=3.0,
        min=0.1,
        max=100.0,
        unit='LENGTH',
        update=live_link_width_right
    )

    link_width: bpy.props.BoolProperty(
        name="Link Width (X)",
        description=(
            "Keep both screens' widths identical - "
            "editing either side updates the other"
        ),
        default=False,
        update=live_link_width_toggled
    )

    left_height: bpy.props.FloatProperty(
        name="Left Height",
        default=1.5,
        min=0.1,
        max=100.0,
        unit='LENGTH',
        update=live_link_height_left
    )

    right_height: bpy.props.FloatProperty(
        name="Right Height",
        default=1.5,
        min=0.1,
        max=100.0,
        unit='LENGTH',
        update=live_link_height_right
    )

    link_height: bpy.props.BoolProperty(
        name="Link Height (Y)",
        description=(
            "Keep both screens' heights identical - "
            "editing either side updates the other"
        ),
        default=True,
        update=live_link_height_toggled
    )

    screen_z: bpy.props.FloatProperty(
        name="Elevation",
        description=(
            "Height of the screens' bottom edge "
            "above the floor"
        ),
        default=0.0,
        min=0.0,
        max=100.0,
        unit='LENGTH'
    )

    corner_angle: bpy.props.FloatProperty(
        name="Corner Angle",
        default=math.radians(90.0),
        min=math.radians(1.0),
        max=math.radians(179.0),
        subtype='ANGLE'
    )

    screen_subdivisions: bpy.props.IntProperty(
        name="Subdivisions",
        description=(
            "Mesh subdivisions of each screen. Higher "
            "values make the projected image smoother "
            "along the screens"
        ),
        default=32,
        min=1,
        max=256
    )

    # ------------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------------

    resolution_multiplier: bpy.props.IntProperty(
        name="Pixels per Meter",
        description=(
            "Screen pixels per meter of screen size. "
            "Sets the left/right render resolutions "
            "1:1 with the screen dimensions and scales "
            "the master render with them"
        ),
        default=640,
        min=16,
        max=8192
    )

    master_resolution_x: bpy.props.IntProperty(
        name="Master X",
        description=(
            "Master render horizontal resolution - "
            "adjust freely to control how much of the "
            "scene around the screens is rendered"
        ),
        default=3840,
        min=64,
        max=16384,
        update=live_render_update
    )

    master_resolution_y: bpy.props.IntProperty(
        name="Master Y",
        description="Master render vertical resolution",
        default=1080,
        min=64,
        max=16384,
        update=live_render_update
    )

    crop_master: bpy.props.BoolProperty(
        name="Crop Master to Screen Area",
        description=(
            "Render only the rectangular region of "
            "the master frame the screens project "
            "onto (a render border fitted to the "
            "screens). The output image keeps the "
            "full master resolution, so the "
            "projection is unaffected"
        ),
        default=True
    )

    crop_margin: bpy.props.IntProperty(
        name="Crop Margin",
        description=(
            "Extra pixels rendered around the "
            "screens' projected area when cropping "
            "the master render"
        ),
        default=8,
        min=0,
        max=512,
        subtype='PIXEL'
    )

    render_percentage: bpy.props.IntProperty(
        name="Render %",
        default=100,
        min=1,
        max=100,
        update=live_render_update
    )

    fps: bpy.props.IntProperty(
        name="FPS",
        default=30,
        min=1,
        max=240,
        update=live_render_update
    )

    transparent_film: bpy.props.BoolProperty(
        name="Transparent Background",
        description=(
            "Render with a transparent background "
            "(Render > Film > Transparent)"
        ),
        default=False,
        update=live_render_update
    )

    output_directory: bpy.props.StringProperty(
        name="Output Directory",
        default="//corner_anamorphic/",
        subtype='DIR_PATH'
    )

    output_mode: bpy.props.EnumProperty(
        name="Mode",
        description=(
            "Write each screen feed as an image "
            "sequence or as a video file"
        ),
        items=(
            (
                'IMAGE',
                "Image Sequence",
                "One image file per frame"
            ),
            (
                'MP4',
                "Video",
                "Video file, requires a Blender "
                "build with FFmpeg"
            )
        ),
        default='IMAGE'
    )

    video_format: bpy.props.EnumProperty(
        name="Format",
        description=(
            "Container format for video output "
            "(codec is chosen to match)"
        ),
        items=(
            ('MPEG4', "MP4", "MPEG-4 container, H.264"),
            ('MKV', "Matroska", "MKV container, H.264"),
            (
                'QUICKTIME',
                "QuickTime",
                "MOV container, H.264"
            ),
            ('AVI', "AVI", "AVI container, H.264"),
            ('WEBM', "WebM", "WebM container, VP9"),
            ('MPEG1', "MPEG-1", ""),
            ('MPEG2', "MPEG-2", ""),
            ('OGG', "Ogg", "Ogg container, Theora"),
            ('DV', "DV", "DV container, DV video")
        ),
        default='MPEG4'
    )

    image_format: bpy.props.EnumProperty(
        name="Format",
        description="File format for image sequences",
        items=(
            ('PNG', "PNG", ""),
            ('JPEG', "JPEG", ""),
            ('JPEG2000', "JPEG 2000", ""),
            ('BMP', "BMP", ""),
            ('TARGA', "Targa", ""),
            ('TARGA_RAW', "Targa Raw", ""),
            ('CINEON', "Cineon", ""),
            ('DPX', "DPX", ""),
            ('OPEN_EXR', "OpenEXR", ""),
            ('HDR', "Radiance HDR", ""),
            ('TIFF', "TIFF", ""),
            ('WEBP', "WebP", "")
        ),
        default='PNG'
    )

    render_layout: bpy.props.EnumProperty(
        name="Layout",
        description=(
            "Render the screens as two separate "
            "feeds or as one seamless wide frame"
        ),
        items=(
            (
                'SEPARATE',
                "Separate",
                "Render left and right as "
                "individual outputs"
            ),
            (
                'COMBINED',
                "Combined",
                "Render both screens as one "
                "seamless wide output"
            )
        ),
        default='SEPARATE'
    )

    left_filename: bpy.props.StringProperty(
        name="Left Filename",
        description=(
            "Output name for the left screen "
            "(extension is added automatically)"
        ),
        default="LEFT"
    )

    right_filename: bpy.props.StringProperty(
        name="Right Filename",
        description=(
            "Output name for the right screen "
            "(extension is added automatically)"
        ),
        default="RIGHT"
    )

    combined_filename: bpy.props.StringProperty(
        name="Filename",
        description=(
            "Output name for the combined wide "
            "render (extension is added automatically)"
        ),
        default="COMBINED"
    )

    # ------------------------------------------------------------------------
    # Generated object names
    # ------------------------------------------------------------------------

    master_camera_name: bpy.props.StringProperty(
        default=""
    )

    left_screen_name: bpy.props.StringProperty(
        default=""
    )

    right_screen_name: bpy.props.StringProperty(
        default=""
    )


# ============================================================================
# UI
# ============================================================================

class CA_PT_main(bpy.types.Panel):

    bl_idname = "VIEW3D_PT_corner_anamorphic"
    bl_label = "Corner Anamorphic"

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    bl_category = "Corner Anamorphic"
    bl_order = 20

    def draw(self, context):

        layout = self.layout
        s = context.scene.corner_anamorphic

        # --------------------------------------------------------------------
        # Viewer
        # --------------------------------------------------------------------

        box = layout.box()
        box.label(text="Viewer", icon='CAMERA_DATA')

        col = box.column(align=True)
        col.prop(s, "viewer_distance")
        col.prop(s, "eye_height")
        col.prop(s, "viewer_angle")
        col.prop(s, "fov")
        col.prop(s, "focus_offset")

        # --------------------------------------------------------------------
        # Screens
        # --------------------------------------------------------------------

        box = layout.box()
        box.label(text="Screens", icon='IMAGE_DATA')

        col = box.column(align=True)

        row = col.row(align=True)
        row.prop(s, "left_width", text="Width L")
        row.prop(
            s,
            "link_width",
            text="",
            icon=(
                'LINKED'
                if s.link_width
                else 'UNLINKED'
            ),
            toggle=True
        )
        row.prop(s, "right_width", text="R")

        row = col.row(align=True)
        row.prop(s, "left_height", text="Height L")
        row.prop(
            s,
            "link_height",
            text="",
            icon=(
                'LINKED'
                if s.link_height
                else 'UNLINKED'
            ),
            toggle=True
        )
        row.prop(s, "right_height", text="R")

        col.prop(s, "screen_z")
        col.prop(s, "corner_angle")
        col.prop(s, "screen_subdivisions")

        # --------------------------------------------------------------------
        # Output
        # --------------------------------------------------------------------

        box = layout.box()
        box.label(text="Output", icon='RENDER_STILL')

        col = box.column(align=True)
        col.prop(s, "resolution_multiplier")

        def output_res(width, height):
            return (
                max(
                    4,
                    int(round(
                        width
                        * s.resolution_multiplier
                    ))
                ),
                max(
                    4,
                    int(round(
                        height
                        * s.resolution_multiplier
                    ))
                )
            )

        if s.render_layout == 'COMBINED':

            res_x, res_y = output_res(
                s.left_width + s.right_width,
                max(s.left_height, s.right_height)
            )

            col.label(
                text=(
                    f"Combined Output: "
                    f"{res_x} x {res_y} px"
                )
            )

        else:

            res_x, res_y = output_res(
                s.left_width,
                s.left_height
            )

            col.label(
                text=f"Left Output: {res_x} x {res_y} px"
            )

            res_x, res_y = output_res(
                s.right_width,
                s.right_height
            )

            col.label(
                text=f"Right Output: {res_x} x {res_y} px"
            )

        row = col.row(align=True)
        row.prop(s, "master_resolution_x")
        row.prop(s, "master_resolution_y")

        row = col.row(align=True)
        row.prop(s, "fps")
        row.prop(s, "render_percentage")

        col.prop(s, "crop_master")

        if s.crop_master:
            col.prop(s, "crop_margin")

        col.prop(s, "transparent_film")

        # --------------------------------------------------------------------
        # Rig
        # --------------------------------------------------------------------

        box = layout.box()
        box.label(text="Rig", icon='CONSTRAINT')

        col = box.column(align=True)
        col.operator(
            "corner_anamorphic.build_rig",
            icon='FILE_REFRESH'
        )

        row = col.row(align=True)
        row.operator(
            "corner_anamorphic.frame_master",
            icon='CAMERA_DATA'
        )
        row.operator(
            "corner_anamorphic.delete_rig",
            icon='TRASH'
        )

        # --------------------------------------------------------------------
        # Rendering
        # --------------------------------------------------------------------

        box = layout.box()
        box.label(text="Rendering", icon='RENDER_ANIMATION')

        col = box.column(align=True)

        row = col.row(align=True)
        row.prop_enum(s, "output_mode", 'IMAGE')
        row.prop_enum(s, "output_mode", 'MP4')

        if s.output_mode == 'IMAGE':
            col.prop(s, "image_format")
        else:
            col.prop(s, "video_format")

        col.separator()

        row = col.row(align=True)
        row.prop_enum(s, "render_layout", 'SEPARATE')
        row.prop_enum(s, "render_layout", 'COMBINED')

        col.separator()

        try:
            display_dir = bpy.path.abspath(
                s.output_directory
            )
        except Exception:
            display_dir = s.output_directory

        row = col.row(align=True)
        row.operator(
            "corner_anamorphic.change_output_directory",
            text="",
            icon='EXPORT'
        )
        row.operator(
            "corner_anamorphic.open_output_directory",
            text="",
            icon='FILE_FOLDER'
        )
        row.label(text=display_dir)

        if s.render_layout == 'COMBINED':

            col.prop(s, "combined_filename")

        else:

            col.prop(s, "left_filename")
            col.prop(s, "right_filename")

        col.separator()

        col.operator(
            "corner_anamorphic.render_master",
            icon='IMAGE_DATA'
        )

        col.separator()

        row = col.row(align=True)
        row.operator(
            "corner_anamorphic.render_master_animation",
            icon='RENDER_ANIMATION'
        )
        row.operator(
            "corner_anamorphic.delete_master_animation",
            text="",
            icon='TRASH'
        )

        row = col.row(align=True)
        row.operator(
            "corner_anamorphic.render_screen_animation",
            icon='RENDER_STILL'
        )
        row.operator(
            "corner_anamorphic.open_render_directory",
            text="",
            icon='FILE_FOLDER'
        )

        # --------------------------------------------------------------------
        # Status
        # --------------------------------------------------------------------

        col.separator()

        if CA_PROGRESS["active"]:

            total = max(1, CA_PROGRESS["total"])
            done = CA_PROGRESS["done"]
            current = min(done + 1, total)

            fraction = frame_progress_fraction()

            if fraction is None:
                fraction = done / total

            col.label(
                text=(
                    f"{CA_PROGRESS['phase']} - "
                    f"frame {current}/{total}"
                )
            )

            progress_ui = getattr(
                col, "progress", None
            )

            if progress_ui:
                progress_ui(
                    factor=fraction,
                    text=f"{int(fraction * 100)}%"
                )
            else:
                col.label(
                    text=f"{int(fraction * 100)}%"
                )

        else:

            col.label(
                text=(
                    "Status: "
                    + (
                        CA_PROGRESS["message"]
                        or "Idle"
                    )
                )
            )


# ============================================================================
# Registration
# ============================================================================

classes = (
    CA_Settings,

    CA_OT_build_rig,
    CA_OT_ffmpeg_warning,
    CA_OT_change_output_directory,
    CA_OT_open_output_directory,
    CA_OT_open_render_directory,
    CA_OT_delete_rig,
    CA_OT_frame_master,

    CA_OT_render_master,
    CA_OT_render_master_animation,
    CA_OT_delete_master_animation,
    CA_OT_render_screen_animation,

    CA_PT_main,
)


def register():

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.corner_anamorphic = (
        bpy.props.PointerProperty(
            type=CA_Settings
        )
    )

    for handler_list, fn in CA_PROGRESS_HANDLERS:
        if fn not in handler_list:
            handler_list.append(fn)

    # Warn once per session when MP4 output can never work (the
    # Microsoft Store build has no FFmpeg).  bpy.data is restricted
    # while the add-on is being enabled, so the check itself runs
    # inside the deferred timer callback, not here.
    if not bpy.app.background:
        bpy.app.timers.register(
            show_ffmpeg_warning_popup,
            first_interval=1.0
        )


def unregister():

    for handler_list, fn in CA_PROGRESS_HANDLERS:
        if fn in handler_list:
            handler_list.remove(fn)

    if hasattr(bpy.types.Scene, "corner_anamorphic"):
        del bpy.types.Scene.corner_anamorphic

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
