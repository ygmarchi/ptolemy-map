from __future__ import annotations

import argparse
from pathlib import Path

from src.ptolemy_map.animate import render_animation, render_svg_animation
from src.ptolemy_map.warp import WarpParams


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
    parser.add_argument("--output", type=Path, default=Path("output/ptolemy_warp.gif"), help="Output GIF path")
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
        "--end-hold-seconds",
        type=float,
        default=1.5,
        help="Seconds to keep the final frame visible",
    )
    parser.add_argument(
        "--gif-loop",
        type=int,
        default=1,
        help="GIF loop count metadata (0=infinite). Default avoids infinite loop.",
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

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    params = WarpParams(lon_factor=args.lon_factor)
    output_suffix = args.output.suffix.lower()
    common_kwargs = dict(
        points_csv=args.points,
        params=params,
        frames=args.frames,
        fps=args.fps,
        slowdown_factor=args.slowdown_factor,
        end_hold_seconds=args.end_hold_seconds,
        loop=args.gif_loop,
        max_labels=args.max_labels,
        hide_americas=not args.show_americas,
    )

    if output_suffix == ".svg":
        render_svg_animation(output_svg=args.output, **common_kwargs)
        print(f"SVG generated in: {args.output}")
        return

    render_animation(output_gif=args.output, **common_kwargs)
    print(f"GIF generated in: {args.output}")


if __name__ == "__main__":
    main()
