from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import searoute as sr

from ports_loader import PortsLoader

app = FastAPI(title="ShipRoute")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ports = PortsLoader()


# ── Port search ──────────────────────────────────────────────────────────────

@app.get("/api/ports/search")
def search_ports(q: str = Query(default="", min_length=0), limit: int = Query(default=10, le=50)):
    results = ports.search(q, limit=limit)
    return results


# ── Route calculation ─────────────────────────────────────────────────────────

class PointInput(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    port_id: Optional[str] = None


class RouteRequest(BaseModel):
    origin: PointInput
    destination: PointInput


def resolve_point(pt: PointInput) -> dict:
    """Return dict with name, lat, lon."""
    if pt.port_id:
        p = ports.get(pt.port_id)
        if not p:
            raise HTTPException(status_code=404, detail=f"Port not found: {pt.port_id}")
        return {"name": p["name"], "lat": p["lat"], "lon": p["lon"]}
    if pt.lat is not None and pt.lon is not None:
        return {"name": f"{pt.lat:.4f}, {pt.lon:.4f}", "lat": pt.lat, "lon": pt.lon}
    raise HTTPException(status_code=422, detail="Provide either port_id or lat/lon coordinates")


@app.post("/api/route")
def calculate_route(req: RouteRequest):
    origin = resolve_point(req.origin)
    destination = resolve_point(req.destination)

    # searoute expects [lon, lat]
    try:
        route = sr.searoute(
            [origin["lon"], origin["lat"]],
            [destination["lon"], destination["lat"]],
            units="naut",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Route calculation failed: {e}")

    distance_nmi: float = route["properties"].get("length", 0)
    distance_km: float = distance_nmi * 1.852
    duration_hours: float = distance_nmi / 14.0  # 14-knot average

    return {
        "route": route,
        "distance_km": round(distance_km, 1),
        "distance_nmi": round(distance_nmi, 1),
        "duration_hours": round(duration_hours, 1),
        "origin": origin,
        "destination": destination,
    }


# ── Static files ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
