#!/usr/bin/env python3
"""
MP-OSC Icon Generator

Draws a "landmark constellation" glyph -- a simplified stick-figure skeleton
in the app's own landmark/connection colors on a dark rounded-square backdrop
-- and exports a full macOS iconset plus the assembled .icns.

The icon is drawn fresh at every required size rather than downscaled from
one master, with per-size stroke/node overrides, so it stays legible at 16px.

Usage:
    uv run python scripts/make_icon.py
    uv run python scripts/make_icon.py --out assets/MP-OSC.icns --png assets/MP-OSC.png
    uv run python scripts/make_icon.py --keep-iconset /tmp/preview.iconset
    uv run python scripts/make_icon.py --check
"""

# ============================================================================
# IMPORTS
# ============================================================================
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw


# ============================================================================
# GEOMETRY
# ============================================================================
# Normalized figure coordinates in a 0..1 box, y growing downward, symmetric
# about x=0.5. Symmetry is deliberate: an asymmetric "action pose" reads
# better at 512px but turns to noise at 16px, and 16px is the hard constraint.
NODES = {
    'head':       (0.500, 0.085),
    'chest':      (0.500, 0.365),
    'hip':        (0.500, 0.605),
    'l_shoulder': (0.310, 0.330),
    'r_shoulder': (0.690, 0.330),
    'l_elbow':    (0.170, 0.445),
    'r_elbow':    (0.830, 0.445),
    'l_hand':     (0.085, 0.700),
    'r_hand':     (0.915, 0.700),
    'l_knee':     (0.330, 0.790),
    'r_knee':     (0.670, 0.790),
    'l_foot':     (0.205, 0.975),
    'r_foot':     (0.795, 0.975),
}

# Polylines connecting the nodes above. Geometry is identical at every size --
# only which nodes get a drawn dot changes with size (see the *_NODES tiers).
CHAINS = (
    ('head', 'chest'),
    ('chest', 'hip'),
    ('chest', 'l_shoulder', 'l_elbow', 'l_hand'),
    ('chest', 'r_shoulder', 'r_elbow', 'r_hand'),
    ('hip', 'l_knee', 'l_foot'),
    ('hip', 'r_knee', 'r_foot'),
)

MINIMAL_NODES = ('head', 'l_hand', 'r_hand', 'l_foot', 'r_foot')
CORE_NODES = MINIMAL_NODES + ('chest', 'hip')
JOINT_NODES = ('l_shoulder', 'r_shoulder', 'l_elbow', 'r_elbow', 'l_knee', 'r_knee')
ALL_NODES = CORE_NODES + JOINT_NODES


# ============================================================================
# STYLE
# ============================================================================
BG_TOP = (30, 33, 41)      # #1E2129
BG_BOTTOM = (14, 16, 20)   # #0E1014

# The app's green-on-dark-grey theme colors (see src/theme.py's PALETTE):
# nodes use the bright accent green so they read as the highlight, lines use
# a dimmer green so they recede behind the nodes.
LANDMARK_RGB = (61, 220, 132)     # #3DDC84 -- node dots (theme accent)
CONNECTION_RGB = (47, 120, 86)    # #2F7856 -- connecting lines

BODY_FRACTION = 0.8047       # 824/1024 -- Big Sur-style rounded-square body
CORNER_FRACTION = 0.2249     # corner radius as a fraction of body size

DEFAULT_STYLE = {
    'inset': 0.174,
    'line_w': 0.045,
    'node_r': 0.050,
    'joint_r': 0.033,
    'head_k': 1.55,
    'dots': ALL_NODES,
}
SIZE_OVERRIDES = {
    16: {'inset': 0.214, 'line_w': 0.100, 'node_r': 0.088, 'head_k': 1.30, 'dots': MINIMAL_NODES},
    32: {'inset': 0.195, 'line_w': 0.078, 'node_r': 0.070, 'head_k': 1.35, 'dots': CORE_NODES},
    64: {'inset': 0.182, 'line_w': 0.060, 'node_r': 0.058, 'joint_r': 0.038, 'dots': ALL_NODES},
}

RENDER_SIZES = (16, 32, 64, 128, 256, 512, 1024)
ICONSET_ENTRIES = (
    ('icon_16x16.png', 16),
    ('icon_16x16@2x.png', 32),
    ('icon_32x32.png', 32),
    ('icon_32x32@2x.png', 64),
    ('icon_128x128.png', 128),
    ('icon_128x128@2x.png', 256),
    ('icon_256x256.png', 256),
    ('icon_256x256@2x.png', 512),
    ('icon_512x512.png', 512),
    ('icon_512x512@2x.png', 1024),
)
SUPERSAMPLE = 4


# ============================================================================
# DRAWING
# ============================================================================
def _style_for(size):
    """Resolve the drawing style for a given render size, with size overrides"""
    style = dict(DEFAULT_STYLE)
    style.update(SIZE_OVERRIDES.get(size, {}))
    return style


def _rounded_mask(px):
    """Alpha mask for the rounded-square body, used to clip the WHOLE flat
    image (background + figure) as a final step -- this is what keeps any
    node or line near an edge from bleeding past the rounded corners and
    producing color fringing on downsample."""
    body = round(px * BODY_FRACTION)
    corner = round(body * CORNER_FRACTION)
    off = (px - body) // 2

    mask = Image.new('L', (px, px), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle([off, off, off + body, off + body], radius=corner, fill=255)
    return mask


def _gradient(px):
    """Vertical background gradient, flat RGB (no alpha)"""
    gradient = Image.new('RGB', (1, px))
    for y in range(px):
        t = y / max(px - 1, 1)
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        gradient.putpixel((0, y), (r, g, b))
    return gradient.resize((px, px))


def _figure_points(px, inset):
    """Map normalized NODES into pixel coordinates inside the inset box"""
    lo = px * inset
    span = px - 2 * lo
    return {name: (lo + x * span, lo + y * span) for name, (x, y) in NODES.items()}


def _draw_segment(draw, p0, p1, width, color):
    """A line with round caps -- Pillow's line() only rounds interior joins"""
    draw.line([p0, p1], fill=color, width=max(1, round(width)))
    r = width / 2
    for cx, cy in (p0, p1):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def _draw_figure(draw, px, style):
    """Draw the connection lines, then the node dots, on top of the backdrop"""
    points = _figure_points(px, style['inset'])
    line_w = px * style['line_w']

    for chain in CHAINS:
        for a, b in zip(chain, chain[1:]):
            _draw_segment(draw, points[a], points[b], line_w, CONNECTION_RGB)

    node_r = px * style['node_r']
    for name in style['dots']:
        cx, cy = points[name]
        r = node_r * (style['head_k'] if name == 'head' else 1.0)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=LANDMARK_RGB)


def render(size):
    """Render one icon size: supersample, draw flat, mask, then LANCZOS downsample

    The background and figure are drawn together as one opaque RGB image, and
    the rounded-square alpha mask is applied only once at the very end. That
    guarantees nothing (a node, a connecting line) can bleed past the rounded
    corners with a jagged edge -- there is exactly one clip, one edge to
    antialias, and downsampling it is just ordinary icon edge softening.
    """
    px = size * SUPERSAMPLE
    style = _style_for(size)

    img = _gradient(px)
    draw = ImageDraw.Draw(img)
    _draw_figure(draw, px, style)

    rgba = img.convert('RGBA')
    rgba.putalpha(_rounded_mask(px))

    return rgba.resize((size, size), Image.LANCZOS)


# ============================================================================
# ICONSET / ICNS
# ============================================================================
def write_iconset(dest_dir):
    """Render every distinct size once and populate a macOS .iconset directory"""
    os.makedirs(dest_dir, exist_ok=True)
    cache = {}
    for filename, size in ICONSET_ENTRIES:
        if size not in cache:
            cache[size] = render(size)
        cache[size].save(os.path.join(dest_dir, filename))
    return dest_dir


def build_icns(iconset_dir, out_path):
    """Assemble a .iconset directory into a .icns via the macOS iconutil tool"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    subprocess.run(
        ['/usr/bin/iconutil', '-c', 'icns', iconset_dir, '-o', out_path],
        check=True,
    )


# ============================================================================
# ENTRY POINT
# ============================================================================
def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate the MP-OSC application icon')
    parser.add_argument('--out', default='assets/MP-OSC.icns', help='Output .icns path')
    parser.add_argument('--png', default='assets/MP-OSC.png', help='Output 1024px PNG master path')
    parser.add_argument('--keep-iconset', default=None,
                        help='Also keep the generated .iconset directory at this path')
    parser.add_argument('--check', action='store_true',
                        help='Advisory only: compare a fresh render against the existing .icns')
    args = parser.parse_args(argv)

    tmp_dir = args.keep_iconset or tempfile.mkdtemp(prefix='mposc-iconset-')
    try:
        write_iconset(tmp_dir)
        build_icns(tmp_dir, args.out)
        print(f"Wrote {args.out}")

        master = render(1024)
        os.makedirs(os.path.dirname(os.path.abspath(args.png)) or '.', exist_ok=True)
        master.save(args.png)
        print(f"Wrote {args.png}")

        if args.keep_iconset:
            print(f"Kept iconset at {args.keep_iconset}")
    finally:
        if not args.keep_iconset and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if args.check:
        print("--check is advisory only; rendering is not guaranteed byte-stable across "
              "Pillow versions. Compare visually instead.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
