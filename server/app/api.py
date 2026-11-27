"""REST API used by the dashboard (cookie-authenticated)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from . import calc
from .auth import COOKIE_NAME, check_ui_key, issue_session, session_valid
from .model import Entity, Instance, SiteModel, load_site_model

router = APIRouter(prefix="/api")

RESOLUTIONS = {"1m": 60, "5m": 300, "1h": 3600}
MAX_RANGE_SECONDS = 31 * 86400
ONLINE_WINDOW = 120

# Counter roles that appear in the Totals table and the energy summary.
ENERGY_ROLES = ("grid_import", "grid_export", "solar_energy", "battery_charge",
                "battery_discharge", "gas", "device_energy")
# Which price slot of the same instance prices a counter role.
PRICE_ROLE_FOR = {"grid_import": "price_import", "grid_export": "price_export", "gas": "price_gas"}


def require_auth(request: Request) -> None:
    if not session_valid(request.app.state.config, request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="authentication required")


def _site(request: Request, site_id: str) -> str:
    if request.app.state.config.site(site_id) is None:
        raise HTTPException(status_code=404, detail="unknown site")
    return site_id


def _range(start: int, end: int) -> tuple[int, int]:
    if end <= start:
        raise HTTPException(status_code=422, detail="end must be after start")
    if end - start > MAX_RANGE_SECONDS:
        raise HTTPException(status_code=422, detail="range too large")
    return start, end


async def _model(request: Request, site_id: str) -> SiteModel:
    st = request.app.state
    return await load_site_model(st.db, st.config.catalogue, site_id)


def _entity_json(e: Entity) -> dict:
    """A sensor that has data. The source (the client's entity id) is never exposed."""
    s = e.sensor
    return {
        "key": e.key, "instance": e.instance, "instance_name": e.instance_name,
        "device": e.device, "device_name": e.device_name, "category": e.category,
        "sensor_id": s.id, "sensor_name": s.name, "name": e.display_name,
        "role": s.role, "kind": s.kind, "unit": s.unit, "sign": s.sign, "color": s.color,
        "group": s.group, "generic": not s.dashboard,
    }


def _instance_json(inst: Instance) -> dict:
    sensors = [{
        "id": e.sensor.id, "key": e.key, "name": e.sensor.name, "role": e.sensor.role,
        "kind": e.sensor.kind, "unit": e.sensor.unit, "sign": e.sensor.sign,
        "color": e.sensor.color, "group": e.sensor.group, "fixed": None,
    } for e in inst.entities]
    sensors += [{
        "id": f.sensor.id, "key": None, "name": f.sensor.name, "role": f.sensor.role,
        "kind": f.sensor.kind, "unit": f.sensor.unit, "sign": f.sensor.sign,
        "color": f.sensor.color, "group": f.sensor.group, "fixed": f.value,
    } for f in inst.fixed]
    return {"id": inst.id, "name": inst.name, "device": inst.device, "device_name": inst.device_name,
            "category": inst.category, "generic": inst.generic, "sensors": sensors}


# ---- auth -------------------------------------------------------------
class LoginBody(BaseModel):
    key: str


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    st = request.app.state
    who = request.client.host if request.client else "unknown"
    if st.login_limiter.blocked(who):
        raise HTTPException(status_code=429, detail="too many attempts, try again later")
    if not check_ui_key(st.config, body.key):
        st.login_limiter.record_failure(who)
        raise HTTPException(status_code=401, detail="invalid key")
    st.login_limiter.reset(who)
    response.set_cookie(
        COOKIE_NAME, issue_session(st.config), max_age=st.config.session_days * 86400,
        httponly=True, secure=st.config.cookie_secure, samesite="lax", path="/")
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", dependencies=[Depends(require_auth)])
async def me():
    return {"ok": True}


# ---- data -------------------------------------------------------------
def _is_online(request: Request, site_id: str, last_seen: float | None, now: float) -> bool:
    return (site_id in request.app.state.ingest_conns
            or (last_seen is not None and now - last_seen < ONLINE_WINDOW))


@router.get("/sites", dependencies=[Depends(require_auth)])
async def sites(request: Request):
    now = time.time()
    out = []
    for s in await request.app.state.db.list_sites():
        if request.app.state.config.site(s["id"]) is None:
            continue
        seen = s["last_seen"]
        out.append({
            "id": s["id"], "name": s["name"], "last_seen": seen,
            "online": _is_online(request, s["id"], seen, now),
            "currency": s["meta"].get("currency", ""),
        })
    return out


def overview_for_site(model: SiteModel, values: dict[str, list[float]], now: float) -> dict:
    """Live grid, solar and battery numbers of one site.

    `values` maps entity key -> [ts, value] (see LiveHub.changes). Values older than
    ONLINE_WINDOW count as missing. A group is None when the site has no fresh value for any of
    its roles, so the UI can show "–".
    """
    def fresh(e) -> float | None:
        v = values.get(e.key)
        return v[1] if v is not None and now - v[0] < ONLINE_WINDOW else None

    def collect(role: str) -> list[float]:
        return [v for e in model.entities if e.role == role and (v := fresh(e)) is not None]

    imp, exp = collect("grid_power_import"), collect("grid_power_export")
    solar, bat, soc = collect("solar_power"), collect("battery_power"), collect("battery_soc")

    capacity = {f.instance: f.value for f in model.fixed
                if f.sensor.role == "battery_capacity" and f.value > 0}
    stored = [calc.stored_kwh(v, capacity[e.instance]) for e in model.entities
              if e.role == "battery_soc" and e.instance in capacity and (v := fresh(e)) is not None]

    grid = None
    if imp or exp:
        i, x = sum(imp), sum(exp)
        grid = {"import_w": i, "export_w": x, "net_w": i - x}
    return {
        "grid": grid,
        "solar": {"power_w": sum(solar)} if solar else None,
        "battery": ({"power_w": sum(bat) if bat else None,
                     "soc": sum(soc) / len(soc) if soc else None,
                     "soc_n": len(soc),
                     "stored_kwh": sum(stored) if stored else None} if bat or soc else None),
    }


async def build_overview(st, now: float) -> list[dict]:
    """Live grid/solar/battery numbers for every configured site (`st` is app.state)."""
    out = []
    for s in await st.db.list_sites():
        if st.config.site(s["id"]) is None:
            continue
        model = await load_site_model(st.db, st.config.catalogue, s["id"])
        seen = s["last_seen"]
        out.append({
            "id": s["id"], "name": s["name"], "last_seen": seen,
            "online": s["id"] in st.ingest_conns or (seen is not None and now - seen < ONLINE_WINDOW),
            **overview_for_site(model, st.hub.changes(s["id"], 0), now),
        })
    return out


@router.get("/overview", dependencies=[Depends(require_auth)])
async def overview(request: Request):
    """Same data as the /ws/overview push, for a single request."""
    return await build_overview(request.app.state, time.time())


@router.get("/sites/{site_id}/devices", dependencies=[Depends(require_auth)])
async def site_devices(request: Request, site_id: str):
    """The site's device instances with their sensors, plus the flat list of sensors with data."""
    _site(request, site_id)
    model = await _model(request, site_id)
    return {"instances": [_instance_json(i) for i in model.instances],
            "entities": [_entity_json(e) for e in model.entities]}


@router.get("/sites/{site_id}/series", dependencies=[Depends(require_auth)])
async def series(request: Request, site_id: str, start: int = Query(ge=0), end: int = Query(ge=0),
                 resolution: str = "1h", keys: str | None = None):
    _site(request, site_id)
    start, end = _range(start, end)
    step = RESOLUTIONS.get(resolution)
    if step is None:
        raise HTTPException(status_code=422, detail="resolution must be 1m, 5m or 1h")
    model = await _model(request, site_id)
    ents = {e.key: e for e in model.entities}
    wanted = list(ents)
    if keys:
        wanted = [k for k in keys.split(",") if k in ents]
    rows = await request.app.state.db.fetch_series(site_id, start, end, step, wanted)
    result: dict[str, dict] = {k: {"kind": ents[k].kind, "points": []} for k in wanted}
    for r in rows:
        entry = result[r["key"]]
        if entry["kind"] == "counter":
            entry["points"].append([r["t"] * 1000, r["delta"]])
        else:
            avg = r["avg_sum"] / r["n"] if r["n"] else None
            entry["points"].append([r["t"] * 1000, avg, r["min"], r["max"]])
    return {"start": start, "end": end, "step": step, "series": result}


def _price_ref(inst: Instance, role: str) -> tuple[str, float | str] | None:
    """The instance's price for `role`: a fixed number or the key of a price sensor."""
    for f in inst.fixed:
        if f.sensor.role == role:
            return "fixed", f.value
    for e in inst.entities:
        if e.role == role:
            return "key", e.key
    return None


async def _collect(request: Request, site_id: str, start: int, end: int):
    """Per-counter totals with cost for [start, end)."""
    db = request.app.state.db
    model = await _model(request, site_id)
    instances = {i.id: i for i in model.instances}
    totals = await db.totals(site_id, start, end)
    sources = []
    for e in model.entities:
        if e.kind != "counter" or e.role not in ENERGY_ROLES:
            continue
        energy = totals.get(e.key, 0.0)
        cost = None
        price_role = PRICE_ROLE_FOR.get(e.role)
        ref = _price_ref(instances[e.instance], price_role) if price_role else None
        if ref and ref[0] == "fixed":
            cost = calc.cost_for([(0, energy)], fixed=ref[1])
        elif ref:
            rows = await db.fetch_series(site_id, start, end, 60, [e.key, ref[1]])
            pts = [(r["t"], r["delta"]) for r in rows if r["key"] == e.key]
            prices = [(r["t"], r["avg_sum"] / r["n"]) for r in rows if r["key"] == ref[1] and r["n"]]
            cost = calc.cost_for(pts, prices)
        s = e.sensor
        sources.append({
            "key": e.key, "instance": e.instance, "name": e.display_name, "role": s.role,
            "group": s.group, "unit": s.unit, "color": s.color, "sign": s.sign,
            "energy": energy, "signed_energy": energy * s.sign,
            "cost": None if cost is None else cost * s.sign,
        })
    return model, sources


@router.get("/sites/{site_id}/totals", dependencies=[Depends(require_auth)])
async def totals(request: Request, site_id: str, start: int = Query(ge=0), end: int = Query(ge=0)):
    _site(request, site_id)
    start, end = _range(start, end)
    _, sources = await _collect(request, site_id, start, end)
    groups: dict[str, dict] = {}
    for s in sources:
        g = groups.setdefault(s["group"], {"unit": s["unit"], "energy": 0.0, "cost": None})
        g["energy"] += s["signed_energy"]
        if s["cost"] is not None:
            g["cost"] = (g["cost"] or 0.0) + s["cost"]
    costs = [g["cost"] for g in groups.values() if g["cost"] is not None]
    meta = next((s for s in await request.app.state.db.list_sites() if s["id"] == site_id), None)
    return {
        "sources": sources, "groups": groups,
        "total_cost": sum(costs) if costs else None,
        "currency": (meta or {}).get("meta", {}).get("currency", ""),
    }


@router.get("/sites/{site_id}/summary", dependencies=[Depends(require_auth)])
async def summary(request: Request, site_id: str, start: int = Query(ge=0), end: int = Query(ge=0)):
    _site(request, site_id)
    start, end = _range(start, end)
    db = request.app.state.db
    model, sources = await _collect(request, site_id, start, end)
    by_role: dict[str, float] = {}
    for s in sources:
        by_role[s["role"]] = by_role.get(s["role"], 0.0) + s["energy"]
    result = calc.compute_summary(by_role)
    # Several batteries: report the mean level (the catalogue gives no capacity to weight by).
    levels = []
    for e in model.entities:
        if e.role == "battery_soc":
            level = await db.last_in_range(site_id, e.key, start, end)
            if level is not None:
                levels.append(level)
    result["battery_soc"] = sum(levels) / len(levels) if levels else None
    present = {e.role for e in model.entities}
    result["has"] = {role: role in present for role in ENERGY_ROLES}
    costs = [s["cost"] for s in sources if s["cost"] is not None]
    result["total_cost"] = sum(costs) if costs else None
    return result


@router.get("/sites/{site_id}/live", dependencies=[Depends(require_auth)])
async def live_snapshot(request: Request, site_id: str):
    _site(request, site_id)
    return {"now": time.time(), "values": request.app.state.hub.changes(site_id, 0)}
