"""
commander.py — Agent 1: mission commander.

Tiles the profile's region of interest into a grid and fans a pool of
scout workers out across the tiles (the visible swarm moment). Every
detection the scouts return is registered on the shared SwarmState
blackboard, which the Commander owns for the whole run.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from contract import SwarmState
from profiles import WatchProfile


@dataclass(frozen=True)
class Tile:
    tile_id: str
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)

    def contains(self, lat: float, lon: float) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.bbox
        return min_lon <= lon < max_lon and min_lat <= lat < max_lat


class Commander:
    def __init__(self, profile: WatchProfile, grid: int = 3, max_workers: int = 6):
        self.profile = profile
        self.grid = grid
        self.max_workers = max_workers
        self.state = SwarmState()

    def tile_region(self) -> list[Tile]:
        min_lon, min_lat, max_lon, max_lat = self.profile.region_bbox
        dlon = (max_lon - min_lon) / self.grid
        dlat = (max_lat - min_lat) / self.grid
        tiles = [
            Tile(
                tile_id=f"T{col}{row}",
                bbox=(
                    min_lon + col * dlon,
                    min_lat + row * dlat,
                    min_lon + (col + 1) * dlon,
                    min_lat + (row + 1) * dlat,
                ),
            )
            for col in range(self.grid)
            for row in range(self.grid)
        ]
        self.state.log(
            f"commander: tiled region {self.profile.region_bbox} into "
            f"{len(tiles)} tiles ({self.grid}x{self.grid})"
        )
        return tiles

    def dispatch(self, tiles: list[Tile], scan_fn) -> int:
        """Fan scan_fn out across the tiles; register every returned Detection."""
        self.state.log(
            f"commander: dispatching {self.max_workers} scouts across {len(tiles)} tiles"
        )
        registered = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(scan_fn, tile): tile for tile in tiles}
            for fut in as_completed(futures):
                tile = futures[fut]
                detections = fut.result()
                self.state.log(f"scout[{tile.tile_id}]: {len(detections)} contact(s)")
                for det in detections:
                    self.state.new_vessel(det)
                    registered += 1
        self.state.log(f"commander: sweep complete — {registered} contacts registered")
        return registered
