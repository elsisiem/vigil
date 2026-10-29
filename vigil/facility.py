"""Facility model: sectors, adjacency, exits and a layout the frontend draws from.

The organizers' incidents use the AWS Builder Loft at 525 Market St. Any other facility id gets
an auto-generated layout from the sector names it mentions, so the station never renders an
empty map.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Sector:
    id: str
    label: str
    x: int
    y: int
    w: int
    h: int
    capacity: int = 0
    is_exit: bool = False
    neighbors: list[str] = field(default_factory=list)


@dataclass
class Facility:
    id: str
    name: str
    address: str
    floor: str
    sectors: dict[str, Sector]
    width: int = 1000
    height: int = 620

    def exits(self) -> list[str]:
        return [s.id for s in self.sectors.values() if s.is_exit]

    def route(self, start: str, avoid: list[str] | None = None) -> list[str]:
        """Shortest path from a sector to the nearest exit, avoiding the given sectors."""
        avoid_set = set(avoid or []) - {start}
        if start not in self.sectors:
            return []
        prev: dict[str, str | None] = {start: None}
        q: deque[str] = deque([start])
        while q:
            cur = q.popleft()
            if self.sectors[cur].is_exit and cur != start:
                path = []
                node: str | None = cur
                while node is not None:
                    path.append(node)
                    node = prev[node]
                return list(reversed(path))
            for n in self.sectors[cur].neighbors:
                if n in prev or n in avoid_set:
                    continue
                prev[n] = cur
                q.append(n)
        return []

    def neighbors_of(self, sector: str) -> list[str]:
        return list(self.sectors.get(sector, Sector(sector, sector, 0, 0, 0, 0)).neighbors)

    def describe(self) -> str:
        lines = [f"{self.name}, {self.address}, {self.floor}. Sectors and adjacency:"]
        for s in self.sectors.values():
            tag = " (EXIT)" if s.is_exit else ""
            cap = f", capacity {s.capacity}" if s.capacity else ""
            lines.append(f"- {s.id}{tag}{cap}: adjacent to {', '.join(s.neighbors) or 'nothing'}")
        return "\n".join(lines)

    def layout(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "floor": self.floor,
            "width": self.width,
            "height": self.height,
            "sectors": [
                {
                    "id": s.id,
                    "label": s.label,
                    "x": s.x,
                    "y": s.y,
                    "w": s.w,
                    "h": s.h,
                    "is_exit": s.is_exit,
                    "capacity": s.capacity,
                    "neighbors": s.neighbors,
                }
                for s in self.sectors.values()
            ],
        }


def _loft() -> Facility:
    s = {
        "stairwell_a": Sector("stairwell_a", "Stair A", 20, 20, 170, 130, 0, True),
        "entrance": Sector("entrance", "Entrance", 20, 160, 170, 440, 40),
        "main_stage": Sector("main_stage", "Main stage", 200, 20, 450, 330, 120),
        "workshop_room": Sector("workshop_room", "Workshop", 660, 20, 320, 190, 40),
        "server_closet": Sector("server_closet", "Server room", 660, 220, 155, 130, 2),
        "restrooms": Sector("restrooms", "Restrooms", 825, 220, 155, 130, 8),
        "catering_coffee_bar": Sector("catering_coffee_bar", "Coffee bar", 200, 360, 260, 240, 35),
        "lounge": Sector("lounge", "Lounge", 470, 360, 260, 240, 30),
        "terrace": Sector("terrace", "Terrace", 740, 360, 125, 240, 25),
        "stairwell_b": Sector("stairwell_b", "Stair B", 875, 360, 105, 240, 0, True),
    }
    adj = {
        "stairwell_a": ["entrance"],
        "entrance": ["stairwell_a", "main_stage", "catering_coffee_bar"],
        "main_stage": ["entrance", "workshop_room", "catering_coffee_bar", "lounge", "server_closet"],
        "workshop_room": ["main_stage", "server_closet", "restrooms"],
        "server_closet": ["main_stage", "workshop_room"],
        "restrooms": ["workshop_room", "terrace"],
        "catering_coffee_bar": ["entrance", "main_stage", "lounge"],
        "lounge": ["catering_coffee_bar", "main_stage", "terrace"],
        "terrace": ["lounge", "restrooms", "stairwell_b"],
        "stairwell_b": ["terrace"],
    }
    for k, v in adj.items():
        s[k].neighbors = v
    return Facility(
        id="aws-builder-loft-sf",
        name="AWS Builder Loft",
        address="525 Market St (First Market Tower), San Francisco, CA 94105",
        floor="2nd floor",
        sectors=s,
    )


FACILITIES: dict[str, Facility] = {"aws-builder-loft-sf": _loft()}


def generic_facility(facility_id: str, sector_ids: list[str]) -> Facility:
    """Grid layout for a facility we have no plan for. Every sector touches its neighbours in the grid."""
    ids = list(dict.fromkeys(sector_ids or ["area"]))
    cols = max(1, min(4, len(ids)))
    cell_w, cell_h = 1000 // cols - 20, 220
    sectors: dict[str, Sector] = {}
    for i, sid in enumerate(ids):
        r, c = divmod(i, cols)
        sectors[sid] = Sector(sid, sid.replace("_", " ").title(), 10 + c * (cell_w + 20), 40 + r * (cell_h + 20), cell_w, cell_h, 0)
    for i, sid in enumerate(ids):
        r, c = divmod(i, cols)
        for j, other in enumerate(ids):
            if i == j:
                continue
            r2, c2 = divmod(j, cols)
            if abs(r - r2) + abs(c - c2) == 1:
                sectors[sid].neighbors.append(other)
    exit_id = "exit"
    sectors[exit_id] = Sector(exit_id, "Exit", 10, 40 + ((len(ids) - 1) // cols + 1) * (cell_h + 20), 200, 80, 0, True)
    sectors[exit_id].neighbors = [ids[0]]
    sectors[ids[0]].neighbors.append(exit_id)
    rows = (len(ids) - 1) // cols + 2
    return Facility(facility_id, facility_id, "unknown address", "unknown floor", sectors, 1000, 40 + rows * (cell_h + 20))


def get_facility(facility_id: str, sector_ids: list[str] | None = None) -> Facility:
    return FACILITIES.get(facility_id) or generic_facility(facility_id, sector_ids or [])
