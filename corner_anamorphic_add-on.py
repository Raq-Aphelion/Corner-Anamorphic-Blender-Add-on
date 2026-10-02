bl_info = {
    "name": "Corner Anamorphic Screen Rig",
    "version": (1, 0, 1),
    "blender": (4, 2, 0),
    "author": "Raq-Auphelion",
    "location": "View3D > Sidebar > Corner Anamorphic",
    "description": "Builds and renders a two-screen 90-degree anamorphic corner installation from one viewer camera.",
    "category": "3D View",
}

import bpy
import math
import os
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
    "message": "Idle"
}


def progress_begin(phase, total):
    CA_PROGRESS.update(
        active=True,
        phase=phase,
        done=0,
        total=max(1, total),
        message=""
    )


def progress_finish(message):
    CA_PROGRESS.update(
        active=False,
        message=message
    )


@persistent
def ca_render_pre(scene):
    CA_PROGRESS["active"] = True


@persistent
def ca_render_post(scene):
    if CA_PROGRESS["active"]:
        CA_PROGRESS["done"] += 1


@persistent
def ca_render_complete(scene):
    CA_PROGRESS["active"] = False


@persistent
def ca_render_cancel(scene):
    CA_PROGRESS["active"] = False
    CA_PROGRESS["message"] = "Cancelled."


CA_PROGRESS_HANDLERS = (
    (bpy.app.handlers.render_pre, ca_render_pre),
    (bpy.app.handlers.render_post, ca_render_post),
    (bpy.app.handlers.render_complete, ca_render_complete),
    (bpy.app.handlers.render_cancel, ca_render_cancel),
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
        + settings.screen_height * 0.5
        + settings.focus_offset
    ))


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


def setup_uv_project(screen, cam, settings):
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
    mod.aspect_x = (
        settings.screen_width
        / max(0.001, settings.screen_height)
    )
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

    z_offset = (
        s.screen_z
        + s.screen_height / 2.0
    )

    right_m = (
        mathutils.Matrix.Translation(
            (0.0, 0.0, z_offset)
        )
        @ rz_right
        @ rx
    )

    left_m = (
        mathutils.Matrix.Translation(
            (0.0, 0.0, z_offset)
        )
        @ rz_left
        @ rx
    )

    x_offset = s.screen_width / 2.0

    right = make_grid(
        OBJ_RIGHT,
        s.screen_width,
        s.screen_height,
        s.screen_subdivisions,
        s.screen_subdivisions,
        right_m,
        coll,
        x_offset=x_offset
    )

    left = make_grid(
        OBJ_LEFT,
        s.screen_width,
        s.screen_height,
        s.screen_subdivisions,
        s.screen_subdivisions,
        left_m,
        coll,
        x_offset=x_offset
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

    half = angle * 0.5
    distance = max(0.001, s.viewer_distance)

    cam.location = (
        -math.cos(half) * distance,
        -math.sin(half) * distance,
        s.eye_height
    )

    # The camera always points at the screens: a focus point on the
    # corner edge where they meet (bottom / center / top).
    look_at(cam, camera_focus_target(s))

    configure_camera(cam, s)

    update_scene_graph(scene)

    # ------------------------------------------------------------------------
    # Screen UVs and camera-driven projection
    # ------------------------------------------------------------------------

    for screen in (left, right):
        setup_screen_uv(screen)
        setup_uv_project(screen, cam, s)

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

    out.collection.objects.link(screen)

    # Orthographic camera, straight-on from the viewer side.
    cam_data = bpy.data.cameras.new(name + "_DATA")
    cam_data.type = 'ORTHO'

    cam = bpy.data.objects.new(
        "CA_OUTPUT_CAMERA_" + side,
        cam_data
    )

    out.collection.objects.link(cam)

    center = screen.matrix_world.translation

    normal = (
        screen.matrix_world.to_3x3()
        @ Vector((0.0, 0.0, 1.0))
    )

    master_cam = bpy.data.objects[s.master_camera_name]

    if normal.dot(master_cam.location - center) < 0.0:
        normal = -normal

    cam.location = (
        center
        + normal * max(
            s.screen_width,
            s.screen_height
        ) * 2.0
    )

    look_at(cam, center)

    # Render aspect always matches the screen aspect (resolutions are
    # tied to the screen size), so the larger dimension is what the
    # ortho scale has to cover.
    cam_data.ortho_scale = max(
        s.screen_width,
        s.screen_height
    )

    out.camera = cam

    out.render.resolution_x = max(
        4,
        int(round(
            s.screen_width
            * s.resolution_multiplier
        ))
    )
    out.render.resolution_y = max(
        4,
        int(round(
            s.screen_height
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

    W = s.screen_width
    H = s.screen_height
    n = max(1, int(s.screen_subdivisions))

    verts = []
    faces = []
    vert_uvs = []

    for quad_index, side in enumerate(
        ("LEFT", "RIGHT")
    ):

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
                x = (quad_index + u - 1.0) * W

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

    W = s.screen_width
    H = s.screen_height

    center = Vector((0.0, H * 0.5, 0.0))

    cam.location = center + Vector((
        0.0,
        0.0,
        max(2.0 * W, H) * 2.0
    ))

    look_at(cam, center)

    cam_data.ortho_scale = max(2.0 * W, H)

    out.camera = cam

    out.render.resolution_x = max(
        4,
        int(round(
            2.0 * W
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


def configure_output_format(out, s, filename):
    """
    Set the output scene up for either MP4 or an image sequence, and
    pick the output filepath.
    """

    output_dir = resolve_output_dir(s)

    os.makedirs(output_dir, exist_ok=True)

    if s.output_mode == 'MP4':

        try:
            out.render.image_settings.file_format = (
                'FFMPEG'
            )
            out.render.ffmpeg.format = 'MPEG4'
            out.render.ffmpeg.codec = 'H264'

        except (TypeError, AttributeError):
            raise RuntimeError(
                "MP4 output requires FFmpeg, which this "
                "Blender build does not include (the "
                "Microsoft Store package).  Switch the "
                "output mode to Image Sequence, or use a "
                "Blender build from blender.org."
            )

        out.render.filepath = os.path.join(
            output_dir,
            os.path.splitext(filename)[0]
        )

    else:

        out.render.image_settings.file_format = (
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

def render_master_still(scene, filepath):
    """
    Render one master frame with the screens hidden.
    """

    s = scene.corner_anamorphic

    cam = bpy.data.objects[s.master_camera_name]
    left = bpy.data.objects[s.left_screen_name]
    right = bpy.data.objects[s.right_screen_name]

    left.hide_render = True
    right.hide_render = True

    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = filepath
    scene.camera = cam

    bpy.ops.render.render(
        'INVOKE_DEFAULT',
        write_still=True
    )


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

    return seq


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


class CA_OT_update(bpy.types.Operator):

    bl_idname = "corner_anamorphic.update_rig"
    bl_label = "Update Projection"

    bl_description = (
        "Apply the current settings "
        "to the existing rig"
    )

    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):

        try:

            update_rig(context.scene)

            self.report(
                {'INFO'},
                "Corner anamorphic rig updated."
            )

            return {'FINISHED'}

        except Exception as exc:

            self.report(
                {'ERROR'},
                f"Update failed: {exc}"
            )

            return {'CANCELLED'}


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

            progress_begin("Master frame", 1)

            render_master_still(scene, path)
            load_master_image(path)

            progress_finish("Master frame rendered.")

            self.report(
                {'INFO'},
                f"Master frame rendered: {path}"
            )

            return {'FINISHED'}

        except Exception as exc:

            progress_finish("Master render failed.")

            self.report(
                {'ERROR'},
                f"Master render failed: {exc}"
            )

            return {'CANCELLED'}


def master_animation_present(scene):
    """
    True when master frames for the scene's current frame range
    exist on disk (the first and last frame are checked).
    """

    s = scene.corner_anamorphic

    try:
        frames_dir = os.path.join(
            resolve_output_dir(s),
            "master_frames"
        )
    except RuntimeError:
        return False

    first_path = os.path.join(
        frames_dir,
        f"master_{scene.frame_start:04d}.png"
    )
    last_path = os.path.join(
        frames_dir,
        f"master_{scene.frame_end:04d}.png"
    )

    return (
        os.path.exists(first_path)
        and os.path.exists(last_path)
    )


def render_master_frames(source, context):
    """
    Render the master camera for the scene frame range into
    <output>/master_frames/ and load the sequence into the
    projection material.
    """

    s = source.corner_anamorphic

    frames_dir = os.path.join(
        resolve_output_dir(s),
        "master_frames"
    )

    os.makedirs(frames_dir, exist_ok=True)

    first = source.frame_start
    last = source.frame_end

    left = bpy.data.objects[s.left_screen_name]
    right = bpy.data.objects[s.right_screen_name]

    left.hide_render = True
    right.hide_render = True

    source.render.image_settings.file_format = 'PNG'
    source.camera = bpy.data.objects[
        s.master_camera_name
    ]

    context.window.scene = source

    progress_begin("Master frames", last - first + 1)

    source.render.filepath = os.path.join(
        frames_dir,
        "master_"
    )

    bpy.ops.render.render(
        'INVOKE_DEFAULT',
        animation=True
    )

    load_master_sequence(frames_dir, first, last)


def render_screen_feeds(source, context):
    """
    Render the screen feeds (separate L/R or the combined wide
    frame) for the scene frame range.  The projection material
    must already point at the master frames.
    """

    s = source.corner_anamorphic

    first = source.frame_start
    last = source.frame_end

    if s.render_layout == 'COMBINED':

        out = build_combined_scene(source)

        configure_output_format(
            out,
            s,
            s.combined_filename
        )

        progress_begin("COMBINED", last - first + 1)

        context.window.scene = out

        bpy.ops.render.render(
            'INVOKE_DEFAULT',
            animation=True
        )

    else:

        for side, filename in (
            ("LEFT", s.left_filename),
            ("RIGHT", s.right_filename)
        ):

            screen = bpy.data.objects[
                s.left_screen_name
                if side == "LEFT"
                else s.right_screen_name
            ]

            out = build_output_scene(source, side)

            configure_output_format(out, s, filename)

            progress_begin(side, last - first + 1)

            screen.hide_render = False

            context.window.scene = out

            bpy.ops.render.render(
                'INVOKE_DEFAULT',
                animation=True
            )

            screen.hide_render = True


class CA_OT_render_master_animation(bpy.types.Operator):

    bl_idname = "corner_anamorphic.render_master_animation"
    bl_label = "Render Master Animation"

    bl_description = (
        "Render the master viewer camera for the scene "
        "frame range; the screens sample these frames"
    )

    bl_options = {'REGISTER'}

    def execute(self, context):

        source = context.scene

        original_frame = source.frame_current
        original_scene = context.window.scene

        try:

            update_rig(source)

            render_master_frames(source, context)

            context.window.scene = original_scene
            source.frame_set(original_frame)

            progress_finish("Master animation rendered.")

            self.report(
                {'INFO'},
                "Master animation rendered."
            )

            return {'FINISHED'}

        except Exception as exc:

            context.window.scene = original_scene
            source.frame_set(original_frame)

            progress_finish("Master animation failed.")

            self.report(
                {'ERROR'},
                f"Master animation failed: {exc}"
            )

            return {'CANCELLED'}


class CA_OT_delete_master_animation(bpy.types.Operator):

    bl_idname = "corner_anamorphic.delete_master_animation"
    bl_label = "Delete Master Animation"

    bl_description = (
        "Delete the rendered master frames and unload "
        "them from the screens"
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

            frames_dir = os.path.join(
                resolve_output_dir(s),
                "master_frames"
            )

            if os.path.isdir(frames_dir):

                for name in os.listdir(frames_dir):

                    if (
                        name.startswith("master_")
                        and name.endswith(".png")
                    ):
                        os.remove(
                            os.path.join(frames_dir, name)
                        )

                try:
                    os.rmdir(frames_dir)
                except OSError:
                    pass

            seq = bpy.data.images.get(IMG_MASTER_SEQ)

            if seq:
                bpy.data.images.remove(seq)

            # Fall back to the still master on the screens.
            tex = get_master_texture_node()

            if tex is not None:
                tex.image = bpy.data.images.get(IMG_MASTER)

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

        source = context.scene
        s = source.corner_anamorphic

        original_frame = source.frame_current
        original_scene = context.window.scene

        try:

            update_rig(source)

            frames_dir = os.path.join(
                resolve_output_dir(s),
                "master_frames"
            )

            load_master_sequence(
                frames_dir,
                source.frame_start,
                source.frame_end
            )

            render_screen_feeds(source, context)

            context.window.scene = original_scene
            source.frame_set(original_frame)

            progress_finish("Screen animation completed.")

            self.report(
                {'INFO'},
                "Screen animation render completed."
            )

            return {'FINISHED'}

        except Exception as exc:

            context.window.scene = original_scene
            source.frame_set(original_frame)

            progress_finish("Screen animation failed.")

            self.report(
                {'ERROR'},
                f"Screen animation failed: {exc}"
            )

            return {'CANCELLED'}


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
    FOV and focus offset apply directly to the existing
    master camera; the UVProject modifiers follow the camera on
    their own.
    """

    try:

        scene = context.scene
        cam = bpy.data.objects.get(
            self.master_camera_name
        )

        if cam is None:
            return

        configure_camera(cam, self)
        look_at(cam, camera_focus_target(self))

        update_scene_graph(scene)

    except Exception:
        pass


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

    screen_width: bpy.props.FloatProperty(
        name="Width",
        default=3.0,
        min=0.1,
        max=100.0,
        unit='LENGTH'
    )

    screen_height: bpy.props.FloatProperty(
        name="Height",
        default=1.5,
        min=0.1,
        max=100.0,
        unit='LENGTH'
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
            "sequence or as an MP4 video"
        ),
        items=(
            (
                'IMAGE',
                "Image Sequence",
                "One image file per frame"
            ),
            (
                'MP4',
                "MP4 (H.264)",
                "Video file, requires a Blender "
                "build with FFmpeg"
            )
        ),
        default='IMAGE'
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
        name="Combined Filename",
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
        col.prop(s, "fov")
        col.prop(s, "focus_offset")

        # --------------------------------------------------------------------
        # Screens
        # --------------------------------------------------------------------

        box = layout.box()
        box.label(text="Screens", icon='IMAGE_DATA')

        col = box.column(align=True)
        col.prop(s, "screen_width")
        col.prop(s, "screen_height")
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

        res_x = max(
            4,
            int(round(
                s.screen_width
                * s.resolution_multiplier
            ))
        )
        res_y = max(
            4,
            int(round(
                s.screen_height
                * s.resolution_multiplier
            ))
        )

        col.label(
            text=f"Screen Output: {res_x} x {res_y} px"
        )

        row = col.row(align=True)
        row.prop(s, "master_resolution_x")
        row.prop(s, "master_resolution_y")

        row = col.row(align=True)
        row.prop(s, "fps")
        row.prop(s, "render_percentage")

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
        col.operator(
            "corner_anamorphic.update_rig",
            icon='CHECKMARK'
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

        col.separator()

        row = col.row(align=True)
        row.prop_enum(s, "render_layout", 'SEPARATE')
        row.prop_enum(s, "render_layout", 'COMBINED')

        col.separator()

        col.prop(s, "output_directory")

        if s.render_layout == 'COMBINED':

            col.prop(s, "combined_filename")

        else:

            row = col.row(align=True)
            row.prop(s, "left_filename")
            row.prop(s, "right_filename")

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

        col.operator(
            "corner_anamorphic.render_screen_animation",
            icon='RENDER_STILL'
        )

        # --------------------------------------------------------------------
        # Status
        # --------------------------------------------------------------------

        col.separator()

        if CA_PROGRESS["active"]:

            total = max(1, CA_PROGRESS["total"])
            done = CA_PROGRESS["done"]
            current = min(done + 1, total)
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
    CA_OT_update,
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
