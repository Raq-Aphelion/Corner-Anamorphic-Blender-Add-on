# Corner Anamorphic

A Blender add-on that builds and renders a two-screen anamorphic corner
installation — the "3D illusion" effect seen on building-corner LED walls —
from a single viewer camera, without any manual render/import/re-render
round trips.

The add-on creates:

- **CA_MASTER_CAMERA** — the viewer camera standing outside the corner,
  always aimed at the screens.
- **CA_SCREEN_LEFT / CA_SCREEN_RIGHT** — the two display surfaces meeting
  at the corner. Each carries a `CA_PROJECTED` UV layer driven by a
  UVProject modifier whose projector is the master camera, so the master
  render appears on the screens exactly as the viewer would see it.

Rendering the screens is done by capturing each screen with a temporary
orthographic camera aimed straight at it (the same result as a manual
two-camera setup), either as two separate feeds or as one seamless wide
combined frame.

## Installation

1. Open Blender (4.2 or newer) and go to
   **Edit > Preferences > Add-ons > Install...**
2. Select `corner_anamorphic_add-on.py`.
3. Enable **Corner Anamorphic Screen Rig**.
4. The panel appears in the 3D viewport sidebar under
   **Corner Anamorphic**.

## Quick start

1. **Save your .blend file first.** The default output directory is
   `//corner_anamorphic/`, relative to the project file — the add-on
   refuses to render while the file is unsaved rather than scattering
   files into the system temp folder.
2. Set up your scene (models, animation, lighting).
3. In the panel, set the **Viewer** and **Screens** values to match your
   physical installation, then press **Build / Rebuild Rig**.
4. Press **Render Screen Preview** to check the current frame on the
   screens.
5. For animation: press **Render Master Animation** once, then
   **Render Screen Animation** to get the final screen feeds.

## Panel reference

### Viewer

- **Distance** — viewer eye distance from the corner.
- **Eye Height** — viewer eye height above the floor.
- **FOV** — master camera horizontal field of view. Used exactly as
  entered; rendering never overwrites it. Adjust freely for framing.
- **Focus Offset** — vertical offset of the camera's focus point from
  the center of the screens' meeting corner.

FOV and focus offset apply live to the existing rig; distance and eye
height are applied on the next rig rebuild.

### Screens

- **Width / Height** — physical size of each screen in meters.
- **Elevation** — height of the screens' bottom edge above the floor.
- **Corner Angle** — angle between the two screens (90° by default).
- **Subdivisions** — mesh subdivisions per screen (default 32). The
  camera-to-screen mapping is projective, so subdivision keeps the
  projected image smooth and unstretched.

Screen settings require **Build / Rebuild Rig** (or any render button,
which rebuilds automatically) to take effect.

### Output

- **Pixels per Meter** — ties each screen's render resolution 1:1 to its
  physical size (e.g. 640 px/m on a 3.0 m x 1.5 m screen gives
  1920 x 960). Shown below as **Screen Output**.
- **Master X / Master Y** — master render resolution. Adjust to control
  how much of the scene around the screens is captured.
- **Render %** — Blender's resolution percentage, applied to all renders.
- **FPS** — frame rate for animation output.
- **Transparent Background** — toggles Blender's
  **Render > Film > Transparent**.

### Rig

- **Build / Rebuild Rig** — creates or rebuilds the camera, screens,
  UVs and projection modifiers from the current settings.
- **Update Projection** — re-applies the projection without a full
  rebuild.
- **View Master** — makes the master camera active and switches
  the viewport to camera view.
- **Delete Rig** — removes everything the add-on created.

### Rendering

- **Mode** — *Image Sequence* (any Blender image format, chosen below)
  or *MP4 (H.264)*. MP4 requires FFmpeg; the Microsoft Store build of
  Blender does not ship it, so use image sequences there (or a
  blender.org build).
- **Layout** — *Separate* renders left and right feeds individually;
  *Combined* renders both screens as one seamless wide frame.
- **Output Directory** — where everything is written.
- **Left / Right Filename** (separate) or **Combined Filename**
  (combined) — output names; the extension is added automatically.

Buttons:

- **Render Screen Preview** — renders the master view for the current
  frame and shows it on the screens (also written as
  `CA_MASTER_FRAME.png`).
- **Render Master Animation** — renders the master camera for the scene
  frame range into `master_frames/`. The **bin button** next to it
  deletes those frames again (only active while a master animation
  exists on disk).
- **Render Screen Animation** — renders the final screen feeds from the
  master frames. Greyed out until a master animation has been rendered.

The line at the bottom shows the active render status (phase, current
frame and progress).

## Output structure

```
<output directory>/
├── CA_MASTER_FRAME.png        # last preview frame
├── master_frames/             # master animation (add-on intermediate)
│   └── master_0001.png ...
├── LEFT_frames/  /  LEFT.mp4  # separate layout
├── RIGHT_frames/ /  RIGHT.mp4
└── COMBINED_frames/ / COMBINED.mp4   # combined layout
```

## Notes

- Renders run through Blender's normal render window, so the UI keeps
  showing progress and you can cancel with Esc as usual.
- If you change the frame range after rendering a master animation,
  re-render the master first — **Render Screen Animation** only checks
  that frames for the current range exist.
- Works with EEVEE and Cycles; the screen material is an emission
  shader displaying the master render.
