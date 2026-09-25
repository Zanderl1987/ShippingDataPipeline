"""Build ``land_path.txt``: the dashboard map's coastlines as one SVG path.

    python -m src.ml.disruption_warning.land_path

Natural Earth 1:50m land (public domain), projected the way the page draws
ports (plain longitude/latitude, 75N to 60S on a 1000 x 440 box), rounded to
0.1 unit and thinned to within ``TOLERANCE`` of the original line. Run once;
the output is committed.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

SOURCE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
          "geojson/ne_50m_land.geojson")
OUT = Path(__file__).with_name("land_path.txt")
# Must match map() in dashboard.html.
WIDTH, HEIGHT, NORTH, SOUTH = 1000, 440, 75.0, -60.0
MIN_POINTS = 4
#: Douglas-Peucker tolerance, in map units (1 unit = 0.36 degrees of longitude).
TOLERANCE = 0.2


def simplify(pts: list[tuple[float, float]], tol: float = TOLERANCE) -> list[tuple[float, float]]:
    """Drop points closer than ``tol`` to the line through their neighbours."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        best, far = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = (abs(dy * (px - ax) - dx * (py - ay)) / norm if norm
                 else ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5)
            if d > best:
                best, far = d, i
        if far >= 0 and best > tol:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [pt for pt, k in zip(pts, keep, strict=True) if k]


def project(lon: float, lat: float) -> tuple[float, float]:
    return (round((lon + 180) / 360 * WIDTH, 1),
            round((NORTH - lat) / (NORTH - SOUTH) * HEIGHT, 1))


def ring_path(ring: list[list[float]]) -> str:
    if all(lat < SOUTH for _, lat in ring) or all(lat > NORTH for _, lat in ring):
        return ""
    pts: list[tuple[float, float]] = []
    for lon, lat in ring:
        pt = project(lon, max(min(lat, NORTH + 5), SOUTH - 5))
        if not pts or pt != pts[-1]:
            pts.append(pt)
    pts = simplify(pts)
    if len(pts) < MIN_POINTS:
        return ""

    def fmt(v: float) -> str:
        return f"{v:.1f}".rstrip("0").rstrip(".")

    head, *rest = pts
    return f"M{fmt(head[0])} {fmt(head[1])}L" + " ".join(
        f"{fmt(x)} {fmt(y)}" for x, y in rest) + "Z"


def build(geojson: dict[str, Any]) -> str:
    parts: list[str] = []
    for feature in geojson["features"]:
        geom = feature["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            parts.extend(ring_path(ring) for ring in poly)
    return "".join(p for p in parts if p)


def main() -> None:
    with urllib.request.urlopen(SOURCE, timeout=60) as resp:  # noqa: S310 (fixed https URL)
        data = json.load(resp)
    path = build(data)
    OUT.write_text(path + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(path) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
