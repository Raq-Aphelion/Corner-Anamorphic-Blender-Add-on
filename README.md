# Corner Anamorphic

A Blender add-on that builds and renders a two-screen anamorphic corner
installation — the "3D illusion" effect seen on building-corner LED walls —
from a single viewer camera, without any manual render/import/re-render
round trips.

It creates a master viewer camera (**CA_MASTER_CAMERA**) and two display
surfaces (**CA_SCREEN_LEFT / CA_SCREEN_RIGHT**) meeting at the corner. A
UVProject modifier ties each screen to the master camera, so the master
render appears on the screens exactly as the viewer would see it. Final
output is captured by an orthographic camera aimed straight at each
screen — the same result as a manual two-camera setup — either as two
separate feeds or as one seamless wide combined frame.

## Installation

1. **Edit > Preferences > Add-ons > Install...**, select
   `corner_anamorphic_add-on.py`, enable **Corner Anamorphic Screen Rig**
   (Blender 4.2+).
2. The panel appears in the 3D viewport sidebar under
   **Corner Anamorphic**.

The Microsoft Store build of Blender has no video (FFmpeg) support; a
popup warns about this on add-on load. Use *Image Sequence* mode there,
or install Blender from blender.org.

## Quick start

1. **Save your .blend file first** — the default output directory
   `//corner_anamorphic/` is relative to the project file.
2. Set the **Viewer** and **Screens** values to match your physical
   installation, then press **Build / Rebuild Rig**.
3. **Render Screen Preview** to check the current frame on the screens.
4. For animation: **Render Master Animation** once, then
   **Render Screen Animation** for the final feeds.

## Panel reference

### Viewer

- **Distance / Eye Height** — viewer position (applied on rig rebuild).
- **Angle** — position along the arc between the screens: 45° centered
  (default), 0° aligned with the right screen's plane, 90° with the
  left's. Applies live.
- **FOV** — master camera field of view, used exactly as entered.
  Applies live.
- **Focus Offset** — vertical offset of the camera's focus point from
  the corner's center. Applies live.

### Screens

- **Width / Height L + R** — physical size of each screen, controlled
  independently. The chain toggles link both screens' widths (off by
  default) or heights (on by default).
- **Elevation** — height of the screens' bottom edge above the floor.
- **Corner Angle** — angle between the screens (90° default).
- **Subdivisions** — mesh subdivisions per screen (default 32); keeps
  the projected image smooth along the screens.

Screen settings take effect on the next rig rebuild (any render button
rebuilds automatically).

### Output

- **Pixels per Meter** — ties each screen's render resolution 1:1 to its
  physical size (640 px/m on a 3.0 m x 1.5 m screen = 1920 x 960). The
  resulting resolution is shown below.
- **Master X / Y** — master render resolution.
- **Crop Master to Screen Area** — limits the master render to the
  rectangular region the screens project onto, skipping pixels the
  screens can never sample. The image keeps its full resolution (black
  outside the region), so the projection is unaffected. **Crop Margin**
  (default 8 px) adds a safety border.
- **Render %**, **FPS**, **Transparent Background** — applied to all
  renders.

### Rig

- **Build / Rebuild Rig** — (re)creates the camera, screens, UVs and
  projection from the current settings.
- **View Master** — switches the viewport to the master camera.
- **Delete Rig** — removes everything the add-on created.

### Rendering

- **Mode** — *Image Sequence* (any image format) or *Video* (MP4,
  Matroska, QuickTime, AVI, WebM, MPEG-1/2, Ogg, DV; the codec is
  picked to match the container).
- **Layout** — *Separate* (individual left/right feeds) or *Combined*
  (one seamless wide frame).
- **Output Directory** — shown read-only; the arrow button changes it,
  the folder button opens it.
- **Filenames** — left/right (separate) or a single name (combined).

The master animation is stored in the Mode's format (PNGs in
`master_frames/`, or one video in `master_video/`). Either kind of
master can drive the feeds in either format afterwards — the loader
prefers the format matching the current mode and falls back to
whichever exists.

Buttons:

- **Render Screen Preview** — renders the current frame and shows it on
  the screens (also written as `CA_MASTER_FRAME.png`).
- **Render Master Animation** — renders the master for the frame range.
  The bin button deletes it again.
- **Render Screen Animation** — renders the final feeds (greyed out
  until a master exists). The folder button opens the most recent
  feed's directory (greyed out until feeds exist).

All renders run through Blender's normal render window with its
progress bar; the panel's status line mirrors the phase, frame number
and per-frame progress. Cancel with Esc as usual.

## Output structure

```
<output directory>/
├── CA_MASTER_FRAME.png        # last preview frame
├── master_frames/             # master animation (image-sequence mode)
├── master_video/              # master animation (video mode)
├── LEFT_frames/  /  LEFT.mp4  # separate layout
├── RIGHT_frames/ /  RIGHT.mp4
└── COMBINED_frames/ / COMBINED.mp4   # combined layout
```

## Notes

- If you change the frame range after rendering a master animation,
  re-render the master first — the feed render only checks that the
  current range exists.
- Works with EEVEE and Cycles; the screen material is an emission
  shader displaying the master render.
