from __future__ import annotations

import html
import json
from pathlib import Path
import re
from typing import Tuple

import geopandas as gpd
import imageio.v2 as imageio
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import geodatasets
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from .warp import ElasticWarpModel, WarpParams, build_elastic_model, warp_geometry, warp_lon_lat


ADDED_POINT_NAMES = {"Isole Canarie", "Arrecife", "Thule Orientale (fittizia)"}
PRIORITY_CITY_NAMES = {"Gerusalemme", "Roma", "Atene", "Alessandria"}


def _load_land() -> gpd.GeoDataFrame:
    land_path = geodatasets.get_path("naturalearth.land")
    land = gpd.read_file(land_path)
    if land.crs is None:
        land = land.set_crs("EPSG:4326")
    else:
        land = land.to_crs("EPSG:4326")
    return land


def _add_canary_profile(land: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add an explicit Canary Islands profile to improve visibility in low-res land datasets."""
    island_centers = [
        (-17.92, 28.72),  # La Palma
        (-17.23, 28.10),  # La Gomera
        (-18.01, 27.73),  # El Hierro
        (-16.57, 28.29),  # Tenerife
        (-15.60, 27.95),  # Gran Canaria
        (-14.01, 28.39),  # Fuerteventura
        (-13.64, 29.04),  # Lanzarote
    ]
    # Degree-radius approximation only for visual contour enrichment.
    canary_polys = [Point(lon, lat).buffer(0.28, resolution=16) for lon, lat in island_centers]
    canary_gdf = gpd.GeoDataFrame(geometry=canary_polys, crs="EPSG:4326")
    return pd.concat([land, canary_gdf], ignore_index=True)


def _remove_americas_mainland(land: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Hide most American mainland shapes while keeping Greenland/Arctic area."""
    bounds = land.geometry.bounds
    lon = (bounds["minx"] + bounds["maxx"]) / 2.0
    lat = (bounds["miny"] + bounds["maxy"]) / 2.0

    # Western hemisphere + non-arctic latitudes approximates the American mainland.
    # Keeping high-lat features avoids removing Greenland, used by the Thule anchor.
    is_americas_mainland = (lon < -20.0) & (lat < 62.0)
    return land.loc[~is_americas_mainland].copy()


def _dms_to_decimal(value: str) -> float:
    text = str(value).strip().upper()
    if not text:
        raise ValueError("Empty coordinate value")

    sign = -1.0 if ("W" in text or "S" in text or text.startswith("-")) else 1.0
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        raise ValueError(f"Unable to parse coordinate: {value!r}")

    degrees = float(numbers[0])
    minutes = float(numbers[1]) if len(numbers) >= 2 else 0.0
    seconds = float(numbers[2]) if len(numbers) >= 3 else 0.0
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    return sign * decimal


def _load_points(points_csv: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    with points_csv.open("r", encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    if not lines:
        raise ValueError("CSV file is empty")

    header_parts = [part.strip() for part in lines[0].split(",")]
    if "Città" not in header_parts or "Latitudini reali" not in header_parts:
        raise ValueError("CSV header must include 'Città' and 'Latitudini reali'")

    for raw_line in lines[1:]:
        if not raw_line.strip():
            continue

        parts = [part.strip() for part in raw_line.split(",")]
        if len(parts) < 5:
            continue

        # The source file may contain stray commas in middle fields; last-but-one is the real longitude.
        city = parts[0]
        lat_raw = parts[1]
        lon_raw = parts[-2]

        try:
            rows.append(
                {
                    "name": city,
                    "lat": _dms_to_decimal(lat_raw),
                    "lon": _dms_to_decimal(lon_raw),
                }
            )
        except ValueError:
            continue

    if not rows:
        raise ValueError("No valid rows parsed from points CSV")

    points = pd.DataFrame(rows)

    # Additional requested locations.
    points = pd.concat(
        [
            points,
            pd.DataFrame(
                [
                    {"name": "Isole Canarie", "lat": 28.4636, "lon": -16.2518},
                    {"name": "Arrecife", "lat": 28.9630, "lon": -13.5477},
                    {
                        "name": "Thule Orientale (fittizia)",
                        "lat": 66.5622,
                        "lon": -36.0,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )

    return points


def _compute_targets(points: pd.DataFrame, params: WarpParams) -> pd.DataFrame:
    out = points.copy()
    out["is_added"] = out["name"].isin(ADDED_POINT_NAMES)

    out["target_lon"] = out["lon"] * params.lon_factor
    out["target_lat"] = out["lat"]

    is_canarie_group = out["name"].isin(["Isole Canarie", "Arrecife"])
    out.loc[is_canarie_group, "target_lat"] = out.loc[is_canarie_group, "lat"] - 15.0

    is_thule = out["name"] == "Thule Orientale (fittizia)"
    out.loc[is_thule, "target_lon"] = out.loc[is_thule, "lon"] + 48.5

    return out


def _select_label_points(points: pd.DataFrame, max_labels: int) -> pd.DataFrame:
    if max_labels <= 0:
        return points.iloc[0:0].copy()

    if len(points) <= max_labels:
        return points.copy()

    # Always keep manually added points and a few anchor cities when present.
    selected_idx: list[int] = []
    for idx, row in points.iterrows():
        if bool(row.get("is_added", False)) or str(row["name"]) in PRIORITY_CITY_NAMES:
            selected_idx.append(int(idx))

    selected_idx = list(dict.fromkeys(selected_idx))
    if len(selected_idx) >= max_labels:
        return points.loc[selected_idx[:max_labels]].copy()

    remaining = [int(i) for i in points.index if int(i) not in selected_idx]
    slots = max_labels - len(selected_idx)
    if slots > 0 and remaining:
        pick = np.linspace(0, len(remaining) - 1, num=min(slots, len(remaining)), dtype=int)
        selected_idx.extend(remaining[i] for i in pick.tolist())

    return points.loc[selected_idx].copy()


def _compute_view_bounds(points: pd.DataFrame, pad_lon: float = 4.0, pad_lat: float = 3.0) -> Tuple[float, float, float, float]:
    lon_all = np.concatenate(
        [
            points["lon"].to_numpy(dtype=float),
            points["target_lon"].to_numpy(dtype=float),
        ]
    )
    lat_all = np.concatenate(
        [
            points["lat"].to_numpy(dtype=float),
            points["target_lat"].to_numpy(dtype=float),
        ]
    )

    min_lon = float(np.min(lon_all)) - pad_lon
    max_lon = float(np.max(lon_all)) + pad_lon
    min_lat = float(np.min(lat_all)) - pad_lat
    max_lat = float(np.max(lat_all)) + pad_lat

    min_lat = max(min_lat, -89.9)
    max_lat = min(max_lat, 89.9)
    return min_lon, max_lon, min_lat, max_lat


def _project_xy(
    lon: np.ndarray,
    lat: np.ndarray,
    view_bounds: Tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray]:
    min_lon, max_lon, min_lat, max_lat = view_bounds
    x = (lon - min_lon) / max(max_lon - min_lon, 1e-9) * width
    y = height - (lat - min_lat) / max(max_lat - min_lat, 1e-9) * height
    return x, y


def _extract_boundary_rings(geom: BaseGeometry) -> list[np.ndarray]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        rings = [np.asarray(geom.exterior.coords)]
        rings.extend(np.asarray(interior.coords) for interior in geom.interiors)
        return rings
    if isinstance(geom, MultiPolygon):
        out: list[np.ndarray] = []
        for poly in geom.geoms:
            out.extend(_extract_boundary_rings(poly))
        return out
    if isinstance(geom, LineString):
        return [np.asarray(geom.coords)]
    if isinstance(geom, MultiLineString):
        return [np.asarray(line.coords) for line in geom.geoms]
    if isinstance(geom, GeometryCollection):
        out: list[np.ndarray] = []
        for part in geom.geoms:
            out.extend(_extract_boundary_rings(part))
        return out
    return []


def _land_to_svg_path(
    land_geoms: gpd.GeoSeries,
    view_bounds: Tuple[float, float, float, float],
    width: int,
    height: int,
) -> str:
    parts: list[str] = []
    for geom in land_geoms:
        for ring in _extract_boundary_rings(geom):
            if ring.shape[0] < 2:
                continue
            x, y = _project_xy(ring[:, 0], ring[:, 1], view_bounds=view_bounds, width=width, height=height)
            cmds = [f"M {x[0]:.2f} {y[0]:.2f}"]
            cmds.extend(f"L {x[i]:.2f} {y[i]:.2f}" for i in range(1, len(x)))
            cmds.append("Z")
            parts.append(" ".join(cmds))
    return " ".join(parts)


def _render_frame(
    land: gpd.GeoDataFrame,
    points: pd.DataFrame,
    label_points: pd.DataFrame,
    model: ElasticWarpModel,
    t: float,
    view_bounds: Tuple[float, float, float, float],
) -> np.ndarray:
    warped_land = land.geometry.apply(lambda geom: warp_geometry(geom, t=t, model=model))

    lon = points["lon"].to_numpy(dtype=float)
    lat = points["lat"].to_numpy(dtype=float)
    warped_lon, warped_lat = warp_lon_lat(lon, lat, t=t, model=model)

    fig, ax = plt.subplots(figsize=(12, 6), dpi=120)
    ax.set_facecolor("#f9faf4")
    fig.patch.set_facecolor("#f9faf4")

    gpd.GeoSeries(warped_land, crs="EPSG:4326").boundary.plot(ax=ax, linewidth=0.65, color="#111111")
    ax.scatter(warped_lon, warped_lat, s=34, color="#cc3d2f", edgecolors="#f9faf4", linewidths=0.6, zorder=5)

    for _, row in label_points.iterrows():
        label_lon_arr, label_lat_arr = warp_lon_lat(
            np.asarray([row["lon"]], dtype=float),
            np.asarray([row["lat"]], dtype=float),
            t=t,
            model=model,
        )
        label_lon = float(label_lon_arr[0])
        label_lat = float(label_lat_arr[0])
        ax.text(
            label_lon + 1.4,
            label_lat + 1.0,
            str(row["name"]),
            fontsize=8,
            color="#1f1f1f",
            family="DejaVu Sans",
        )

    min_lon, max_lon, min_lat, max_lat = view_bounds
    ax.set_xlim(min_lon, max_lon)
    ax.set_ylim(min_lat, max_lat)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    fig.canvas.draw()
    width, height = fig.canvas.get_width_height()
    frame = np.frombuffer(fig.canvas.buffer_rgba(), dtype=np.uint8).reshape(height, width, 4)[..., :3]
    plt.close(fig)
    return frame


def render_animation(
    points_csv: Path,
    output_gif: Path,
    params: WarpParams,
    frames: int = 80,
    fps: int = 20,
    slowdown_factor: float = 5.0,
    end_hold_seconds: float = 1.5,
    loop: int = 1,
    max_labels: int = 18,
    hide_americas: bool = True,
) -> None:
    land = _add_canary_profile(_load_land())
    if hide_americas:
        land = _remove_americas_mainland(land)
    points = _compute_targets(_load_points(points_csv), params=params)
    label_points = _select_label_points(points, max_labels=max_labels)
    view_bounds = _compute_view_bounds(points)
    model = build_elastic_model(
        control_lon=points["lon"].to_numpy(dtype=float),
        control_lat=points["lat"].to_numpy(dtype=float),
        target_lon=points["target_lon"].to_numpy(dtype=float),
        target_lat=points["target_lat"].to_numpy(dtype=float),
        params=params,
    )

    output_gif.parent.mkdir(parents=True, exist_ok=True)

    all_frames = []
    effective_frames = max(2, int(round(frames * max(slowdown_factor, 1.0))))

    for idx in range(effective_frames):
        t = 0.0 if effective_frames <= 1 else idx / (effective_frames - 1)
        all_frames.append(
            _render_frame(
                land,
                points,
                label_points=label_points,
                model=model,
                t=t,
                view_bounds=view_bounds,
            )
        )

    hold_count = max(0, int(round(end_hold_seconds * fps)))
    if hold_count > 0:
        all_frames.extend([all_frames[-1]] * hold_count)

    imageio.mimsave(output_gif, all_frames, fps=fps, loop=loop)


def render_svg_animation(
        points_csv: Path,
        output_svg: Path,
        params: WarpParams,
        frames: int = 80,
        fps: int = 20,
        slowdown_factor: float = 5.0,
        end_hold_seconds: float = 1.5,
        loop: int = 1,
        max_labels: int = 18,
        hide_americas: bool = True,
        width: int = 1200,
        height: int = 700,
) -> None:
        land = _add_canary_profile(_load_land())
        if hide_americas:
                land = _remove_americas_mainland(land)

        points = _compute_targets(_load_points(points_csv), params=params)
        label_points = _select_label_points(points, max_labels=max_labels)
        view_bounds = _compute_view_bounds(points)
        model = build_elastic_model(
                control_lon=points["lon"].to_numpy(dtype=float),
                control_lat=points["lat"].to_numpy(dtype=float),
                target_lon=points["target_lon"].to_numpy(dtype=float),
                target_lat=points["target_lat"].to_numpy(dtype=float),
                params=params,
        )

        effective_frames = max(2, int(round(frames * max(slowdown_factor, 1.0))))
        hold_count = max(0, int(round(end_hold_seconds * fps)))
        total_frames = effective_frames + hold_count

        land_paths: list[str] = []
        points_frames: list[list[dict[str, float]]] = []
        labels_frames: list[list[dict[str, str]]] = []

        for idx in range(effective_frames):
                t = 0.0 if effective_frames <= 1 else idx / (effective_frames - 1)
                warped_land = land.geometry.apply(lambda geom: warp_geometry(geom, t=t, model=model))
                land_paths.append(_land_to_svg_path(gpd.GeoSeries(warped_land, crs="EPSG:4326"), view_bounds, width, height))

                lon = points["lon"].to_numpy(dtype=float)
                lat = points["lat"].to_numpy(dtype=float)
                warped_lon, warped_lat = warp_lon_lat(lon, lat, t=t, model=model)
                px, py = _project_xy(warped_lon, warped_lat, view_bounds=view_bounds, width=width, height=height)
                points_frames.append([{"x": float(px[i]), "y": float(py[i])} for i in range(len(px))])

                frame_labels: list[dict[str, str]] = []
                for _, row in label_points.iterrows():
                        llon, llat = warp_lon_lat(
                                np.asarray([row["lon"]], dtype=float),
                                np.asarray([row["lat"]], dtype=float),
                                t=t,
                                model=model,
                        )
                        lx, ly = _project_xy(llon, llat, view_bounds=view_bounds, width=width, height=height)
                        frame_labels.append(
                                {
                                        "x": f"{float(lx[0] + 8.0):.2f}",
                                        "y": f"{float(ly[0] - 8.0):.2f}",
                                        "text": html.escape(str(row["name"])),
                                }
                        )
                labels_frames.append(frame_labels)

        if hold_count > 0:
                land_paths.extend([land_paths[-1]] * hold_count)
                points_frames.extend([points_frames[-1]] * hold_count)
                labels_frames.extend([labels_frames[-1]] * hold_count)

        output_svg.parent.mkdir(parents=True, exist_ok=True)

        initial_points_svg = "\n".join(
                f'<circle cx="{p["x"]:.2f}" cy="{p["y"]:.2f}" r="3.2" />' for p in points_frames[0]
        )
        initial_labels_svg = "\n".join(
                f'<text x="{lbl["x"]}" y="{lbl["y"]}">{lbl["text"]}</text>' for lbl in labels_frames[0]
        )

        svg = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {width} {height}\" width=\"{width}\" height=\"{height}\">
    <rect x=\"0\" y=\"0\" width=\"{width}\" height=\"{height}\" fill=\"#f9faf4\"/>
    <path id=\"land\" d=\"{land_paths[0]}\" fill=\"none\" stroke=\"#111111\" stroke-width=\"1.1\"/>
    <g id=\"points\" fill=\"#cc3d2f\" stroke=\"#f9faf4\" stroke-width=\"0.8\">{initial_points_svg}</g>
    <g id=\"labels\" fill=\"#1f1f1f\" font-size=\"12\" font-family=\"DejaVu Sans\">{initial_labels_svg}</g>
    <script><![CDATA[
        const frameDuration = {int(round(1000 / max(fps, 1)))};
        const totalFrames = {total_frames};
        const loopCount = {loop};
        const landPaths = {json.dumps(land_paths)};
        const pointsFrames = {json.dumps(points_frames)};
        const labelsFrames = {json.dumps(labels_frames)};

        const landNode = document.getElementById('land');
        const pointsNode = document.getElementById('points');
        const labelsNode = document.getElementById('labels');

        let frame = 0;
        let played = 0;

        function renderFrame(idx) {{
            landNode.setAttribute('d', landPaths[idx]);
            pointsNode.innerHTML = pointsFrames[idx].map(p =>
                `<circle cx="${{p.x.toFixed(2)}}" cy="${{p.y.toFixed(2)}}" r="3.2" />`
            ).join('');
            labelsNode.innerHTML = labelsFrames[idx].map(l =>
                `<text x="${{l.x}}" y="${{l.y}}">${{l.text}}</text>`
            ).join('');
        }}

        renderFrame(0);
        const timer = setInterval(() => {{
            frame += 1;
            if (frame >= totalFrames) {{
                played += 1;
                if (loopCount === 0 || played < loopCount) {{
                    frame = 0;
                }} else {{
                    frame = totalFrames - 1;
                    renderFrame(frame);
                    clearInterval(timer);
                    return;
                }}
            }}
            renderFrame(frame);
        }}, frameDuration);
    ]]></script>
</svg>
"""
        output_svg.write_text(svg, encoding="utf-8")
