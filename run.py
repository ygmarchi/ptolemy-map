from __future__ import annotations

import argparse
from pathlib import Path

from src.ptolemy_map.animate import render_animation, render_svg_animation
from src.ptolemy_map.warp import WarpParams


# Optimal alignment of "Ptolemy world.png" (937x836) onto the final animation frame (1440x720).
# Derived from GIMP Unified Transform matrix (2025-04-05):
#   [ 1.5169  0.4229  -747.0543 ]
#   [-0.4229  1.5169    98.1184 ]
#   [ 0.0000  0.0000     1.0000 ]
# Applied as a single affine transformation (scale + rotation + translation simultaneously).
# White background fill happens AFTER transformation.
YOUTUBE_GIMP_AFFINE_A: float = 1.5169
YOUTUBE_GIMP_AFFINE_B: float = 0.4229
YOUTUBE_GIMP_AFFINE_C: float = -0.4229
YOUTUBE_GIMP_AFFINE_D: float = 1.5169
YOUTUBE_GIMP_AFFINE_TX: float = -700.0543
YOUTUBE_GIMP_AFFINE_TY: float = 98.1184


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render an animated geographic warp over land boundaries and points.",
    )
    parser.add_argument(
        "--points",
        type=Path,
        default=Path("data/Coordinate città campione.csv"),
        help="CSV source (uses Città, Latitudini reali, Longitudini reali da Greenwich)",
    )
    parser.add_argument("--output", type=Path, default=Path("output/ptolemy_warp.mp4"), help="Output video path (MP4 or GIF by extension)")
    parser.add_argument("--frames", type=int, default=80, help="Base number of frames before slowdown scaling")
    parser.add_argument("--fps", type=int, default=20, help="Frames per second")
    parser.add_argument(
        "--lon-factor",
        type=float,
        default=1.428,
        help="Target transformation factor: lon' = lon * lon-factor",
    )
    parser.add_argument(
        "--slowdown-factor",
        type=float,
        default=5.0,
        help="Slowdown multiplier for transformation progress (default: 5x)",
    )
    parser.add_argument(
        "--start-hold-seconds",
        type=float,
        default=0.0,
        help="Seconds to hold on the initial frame",
    )
    parser.add_argument(
        "--end-hold-seconds",
        type=float,
        default=1.5,
        help="Seconds to keep the final frame visible",
    )
    parser.add_argument(
        "--gif-loop",
        type=int,
        default=1,
        help="GIF loop count metadata (0=infinite). Default avoids infinite loop. (Only for .gif output)",
    )
    parser.add_argument(
        "--max-labels",
        type=int,
        default=18,
        help="Maximum number of city labels rendered on the map (0 disables labels)",
    )
    parser.add_argument(
        "--show-americas",
        action="store_true",
        help="Render American mainland too (default hides it)",
    )
    parser.add_argument(
        "--youtube-mode",
        action="store_true",
        help="Enable YouTube-optimized settings (5s hold start, 5s hold end, dissolve to Ptolemy world.png, 10s final hold)",
    )
    parser.add_argument(
        "--final-image",
        type=Path,
        default=None,
        help="Path to final image for dissolve transition (used with --youtube-mode or standalone)",
    )
    parser.add_argument(
        "--final-image-duration",
        type=float,
        default=10.0,
        help="Seconds to display final image",
    )
    parser.add_argument(
        "--final-image-dissolve-seconds",
        type=float,
        default=3.0,
        help="Seconds for dissolve transition from last animation frame to final image",
    )
    parser.add_argument(
        "--final-image-affine-a",
        type=float,
        default=1.0,
        help="Affine matrix element [0,0] (default: 1.0 = no transform)",
    )
    parser.add_argument(
        "--final-image-affine-b",
        type=float,
        default=0.0,
        help="Affine matrix element [0,1]",
    )
    parser.add_argument(
        "--final-image-affine-c",
        type=float,
        default=0.0,
        help="Affine matrix element [1,0]",
    )
    parser.add_argument(
        "--final-image-affine-d",
        type=float,
        default=1.0,
        help="Affine matrix element [1,1]",
    )
    parser.add_argument(
        "--final-image-affine-tx",
        type=float,
        default=0.0,
        help="Affine translation x",
    )
    parser.add_argument(
        "--final-image-affine-ty",
        type=float,
        default=0.0,
        help="Affine translation y",
    )
    parser.add_argument(
        "--export-final-frame",
        action="store_true",
        help="Export the final animation frame as reference image (output/final_frame_reference.png)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    params = WarpParams(lon_factor=args.lon_factor)
    output_suffix = args.output.suffix.lower()
    
    # Setup final image transformation
    start_hold_seconds = args.start_hold_seconds
    end_hold_seconds = args.end_hold_seconds
    final_image = args.final_image
    final_image_duration = args.final_image_duration
    final_image_dissolve_seconds = args.final_image_dissolve_seconds
    
    # Affine transformation matrix components
    affine_a = args.final_image_affine_a
    affine_b = args.final_image_affine_b
    affine_c = args.final_image_affine_c
    affine_d = args.final_image_affine_d
    affine_tx = args.final_image_affine_tx
    affine_ty = args.final_image_affine_ty

    if args.youtube_mode:
        start_hold_seconds = 5.0
        end_hold_seconds = 5.0
        if final_image is None:
            final_image = Path("Ptolemy world.png")
        final_image_duration = 10.0
        final_image_dissolve_seconds = 3.0
        # Use calibrated alignment from GIMP matrix unless user overrode them
        if args.final_image_affine_a == 1.0 and args.final_image_affine_d == 1.0:
            affine_a = YOUTUBE_GIMP_AFFINE_A
            affine_b = YOUTUBE_GIMP_AFFINE_B
            affine_c = YOUTUBE_GIMP_AFFINE_C
            affine_d = YOUTUBE_GIMP_AFFINE_D
            affine_tx = YOUTUBE_GIMP_AFFINE_TX
            affine_ty = YOUTUBE_GIMP_AFFINE_TY
    
    common_kwargs = dict(
        points_csv=args.points,
        params=params,
        frames=args.frames,
        fps=args.fps,
        slowdown_factor=args.slowdown_factor,
        start_hold_seconds=start_hold_seconds,
        end_hold_seconds=end_hold_seconds,
        loop=args.gif_loop,
        max_labels=args.max_labels,
        hide_americas=not args.show_americas,
        final_image=final_image,
        final_image_duration=final_image_duration,
        final_image_dissolve_seconds=final_image_dissolve_seconds,
        final_image_affine_a=affine_a,
        final_image_affine_b=affine_b,
        final_image_affine_c=affine_c,
        final_image_affine_d=affine_d,
        final_image_affine_tx=affine_tx,
        final_image_affine_ty=affine_ty,
        export_final_frame=args.export_final_frame,
    )

    if output_suffix == ".svg":
        render_svg_animation(output_svg=args.output, **common_kwargs)
        print(f"SVG generated in: {args.output}")
        return

    render_animation(output_gif=args.output, **common_kwargs)
    format_name = "MP4 video" if output_suffix == ".mp4" else "GIF"
    print(f"{format_name} generated in: {args.output}")


if __name__ == "__main__":
    main()
