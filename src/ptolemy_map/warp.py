from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


@dataclass(frozen=True)
class WarpParams:
    lon_factor: float = 1.428


@dataclass(frozen=True)
class ElasticWarpModel:
    control_lon: np.ndarray
    control_lat: np.ndarray
    delta_lon: np.ndarray
    delta_lat: np.ndarray
    tps_coef_lon: np.ndarray
    tps_coef_lat: np.ndarray


def _clip_latitude(lat: np.ndarray) -> np.ndarray:
    return np.clip(lat, -89.9, 89.9)


def _tps_kernel(r2: np.ndarray) -> np.ndarray:
    # Thin-plate spline radial basis: U(r) = r^2 * log(r), with U(0)=0.
    safe_r2 = np.maximum(r2, 1e-18)
    return safe_r2 * np.log(np.sqrt(safe_r2))


def build_elastic_model(
    control_lon: np.ndarray,
    control_lat: np.ndarray,
    target_lon: np.ndarray,
    target_lat: np.ndarray,
    params: WarpParams,
) -> ElasticWarpModel:
    """Build a TPS-based elastic model from control points.

    The model interpolates displacements exactly at control points and has global support,
    so far areas can still be dragged by large anchor shifts (e.g. Thule).
    """
    control_lon = np.asarray(control_lon, dtype=float)
    control_lat = np.asarray(control_lat, dtype=float)
    target_lon = np.asarray(target_lon, dtype=float)
    target_lat = np.asarray(target_lat, dtype=float)

    if not (len(control_lon) == len(control_lat) == len(target_lon) == len(target_lat)):
        raise ValueError("Control/target arrays must have the same length")

    if len(control_lon) == 0:
        raise ValueError("At least one control point is required")

    delta_lon = target_lon - control_lon
    delta_lat = target_lat - control_lat

    x = control_lon
    y = control_lat
    n = len(control_lon)

    dlon = x[:, None] - x[None, :]
    dlat = y[:, None] - y[None, :]
    r2 = dlon * dlon + dlat * dlat
    k = _tps_kernel(r2)

    p = np.column_stack([np.ones(n), x, y])
    a = np.zeros((n + 3, n + 3), dtype=float)
    a[:n, :n] = k
    a[:n, n:] = p
    a[n:, :n] = p.T

    b_lon = np.zeros(n + 3, dtype=float)
    b_lat = np.zeros(n + 3, dtype=float)
    b_lon[:n] = delta_lon
    b_lat[:n] = delta_lat

    coef_lon = np.linalg.solve(a, b_lon)
    coef_lat = np.linalg.solve(a, b_lat)

    return ElasticWarpModel(
        control_lon=control_lon,
        control_lat=control_lat,
        delta_lon=delta_lon,
        delta_lat=delta_lat,
        tps_coef_lon=coef_lon,
        tps_coef_lat=coef_lat,
    )


def _weighted_displacement(
    lon: np.ndarray,
    lat: np.ndarray,
    model: ElasticWarpModel,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(lon, dtype=float)
    y = np.asarray(lat, dtype=float)
    dlon = x[:, None] - model.control_lon[None, :]
    dlat = y[:, None] - model.control_lat[None, :]
    r2 = dlon * dlon + dlat * dlat
    u = _tps_kernel(r2)

    n = len(model.control_lon)
    w_lon = model.tps_coef_lon[:n]
    a_lon = model.tps_coef_lon[n:]
    w_lat = model.tps_coef_lat[:n]
    a_lat = model.tps_coef_lat[n:]

    disp_lon = u @ w_lon + (a_lon[0] + a_lon[1] * x + a_lon[2] * y)
    disp_lat = u @ w_lat + (a_lat[0] + a_lat[1] * x + a_lat[2] * y)

    return disp_lon, disp_lat


def warp_lon_lat(lon: np.ndarray, lat: np.ndarray, t: float, model: ElasticWarpModel) -> tuple[np.ndarray, np.ndarray]:
    """Apply elastic displacement induced by control points."""
    disp_lon, disp_lat = _weighted_displacement(lon, lat, model)
    out_lon = lon + t * disp_lon
    out_lat = _clip_latitude(lat + t * disp_lat)
    return out_lon, out_lat


def warp_geometry(geometry: BaseGeometry, t: float, model: ElasticWarpModel) -> BaseGeometry:
    """Warp any shapely geometry by transforming each lon/lat vertex."""

    def _warp(x, y, z=None):
        x_arr = np.asarray(x, dtype=float)
        y_arr = np.asarray(y, dtype=float)
        out_x, out_y = warp_lon_lat(x_arr, y_arr, t=t, model=model)

        if z is None:
            return out_x, out_y
        return out_x, out_y, z

    return transform(_warp, geometry)
