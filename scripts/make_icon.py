#!/usr/bin/env python3
"""
Gesture Icon Generator

Draws one asymmetric pose (left arm raised, right arm level, right foot
stepping out) as landmark nodes inside a thin measuring circle on a dark
rounded-square backdrop, and exports a full macOS iconset plus the .icns.

The icon is drawn fresh at every required size with detail tiers (see
src/icon_geometry.py), so it stays legible at 16px.

Usage:
    uv run python scripts/make_icon.py
    uv run python scripts/make_icon.py --out assets/Gesture.icns --png assets/Gesture.png
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

# Geometry, tiers and colours live in src/icon_geometry.py so the runtime UI can
# draw the same mark. Run as a script, only scripts/ is on sys.path, so add the
# repo root first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.icon_geometry import (  # noqa: E402
    ACCENT, BG_BOTTOM, BG_TOP, CIRCLE, CIRCLE_ALPHA, CIRCLE_WIDTH, CORNER, DIM,
    TIERS, scaled_point, tier_for,
)


# ============================================================================
# CONSTANTS
# ============================================================================
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
SUPERSAMPLE = 8


# ============================================================================
# DRAWING
# ============================================================================
def _rounded_mask(px):
    """Alpha mask for the rounded-square body, applied once at the very end so
    nothing can bleed past the corners."""
    mask = Image.new('L', (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, px - 1, px - 1], radius=int(CORNER * px), fill=255)
    return mask


def _gradient(px):
    """Vertical background gradient, flat RGB (no alpha)"""
    img = Image.new('RGB', (px, px))
    draw = ImageDraw.Draw(img)
    for y in range(px):
        t = y / max(1, px - 1)
        draw.line([(0, y), (px, y)],
                  fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    return img


def render(size, ss=SUPERSAMPLE):
    """Render one icon size: supersample, draw flat, mask, then LANCZOS downsample

    Args:
        size: Output edge length in pixels; picks the detail tier.
        ss: Supersampling factor.

    Returns:
        RGBA PIL image of size x size.
    """
    tier = TIERS[tier_for(size)]
    scale = tier.scale
    px = size * ss
    unit = px / 100.0
    img = _gradient(px)
    draw = ImageDraw.Draw(img)

    def pt(p):
        x, y = scaled_point(p, scale)
        return (x * unit, y * unit)

    if tier.circle:
        cx, cy = pt((CIRCLE[0], CIRCLE[1]))
        rr = CIRCLE[2] * scale * unit
        ring = Image.new('RGBA', img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                                     outline=DIM, width=max(1, int(CIRCLE_WIDTH * unit)))
        ring.putalpha(ring.getchannel('A').point(lambda a: int(a * CIRCLE_ALPHA)))
        img = Image.alpha_composite(img.convert('RGBA'), ring).convert('RGB')
        draw = ImageDraw.Draw(img)

    w = max(1, int(tier.weight * scale * unit))
    for chain in tier.bones:
        draw.line([pt(p) for p in chain], fill=DIM, width=w, joint='curve')
        for p in chain:  # round caps
            x, y = pt(p)
            draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=DIM)

    for p, r in tier.nodes:
        x, y = pt(p)
        rr = r * scale * unit
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=ACCENT)

    img = img.convert('RGBA')
    img.putalpha(_rounded_mask(px))
    return img.resize((size, size), Image.LANCZOS)


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
    parser = argparse.ArgumentParser(description='Generate the Gesture application icon')
    parser.add_argument('--out', default='assets/Gesture.icns', help='Output .icns path')
    parser.add_argument('--png', default='assets/Gesture.png', help='Output 1024px PNG master path')
    parser.add_argument('--keep-iconset', default=None,
                        help='Also keep the generated .iconset directory at this path')
    parser.add_argument('--check', action='store_true',
                        help='Advisory only: compare a fresh render against the existing .icns')
    args = parser.parse_args(argv)

    # iconutil rejects directories that don't end in .iconset
    scratch = None if args.keep_iconset else tempfile.mkdtemp(prefix='gesture-icon-')
    tmp_dir = args.keep_iconset or os.path.join(scratch, 'Gesture.iconset')
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
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    if args.check:
        print("--check is advisory only; rendering is not guaranteed byte-stable across "
              "Pillow versions. Compare visually instead.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
