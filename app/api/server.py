"""Aplicação web (FastAPI): painel, API REST e WebSocket.

Segurança: o servidor escuta apenas em 127.0.0.1 (inacessível por outros
computadores da rede). Além disso, só aceita requisições endereçadas a
localhost/127.0.0.1 e recusa ações (POST/WebSocket) vindas de outros sites
abertos no navegador.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, paths
from ..config import CONNECTION_MODES, SettingsStore
from ..monitor.dish_api import DishUnavailable
from ..monitor.engine import MonitorEngine, now_ms
from ..reports import builder
from ..services import analytics
from ..storage.db import Database
from .realtime import Hub

log = logging.getLogger(__name__)

NO_CACHE = {"Cache-Control": "no-store"}


class ModeBody(BaseModel):
    mode: str


class ProviderBody(BaseModel):
    type: str
    name: str = ""
    equip_host: Optional[str] = None     # None = endereço padrão do tipo; "" = não monitorar
    apply_thresholds: bool = True


def create_app(store: SettingsStore, db: Database, prober: Any,
               request_shutdown: Optional[Callable[[], None]] = None,
               autostart_engine: bool = True, net_reader: Any = None,
               dish_source_factory: Any = None) -> FastAPI:
    hub = Hub()
    engine = MonitorEngine(store, db, prober, hub.broadcast, net_reader=net_reader,
                           dish_source_factory=dish_source_factory)
    port = store.settings.port
    allowed_hosts = {f"localhost:{port}", f"127.0.0.1:{port}", "localhost", "127.0.0.1",
                     "testserver"}
    allowed_origins = {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if autostart_engine:
            await engine.start()
        try:
            yield
        finally:
            await engine.stop("monitoramento encerrado")
            db.close()

    app = FastAPI(title="Monitor de Internet", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.state.hub = hub

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return JSONResponse({"detail": "host não permitido"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin is not None and origin not in allowed_origins:
                return JSONResponse({"detail": "origem não permitida"}, status_code=403)
        return await call_next(request)

    # ---------------------------------------------------------- páginas
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(paths.WEB_DIR / "index.html", headers=NO_CACHE)

    @app.get("/historico", include_in_schema=False)
    async def page_history():
        return FileResponse(paths.WEB_DIR / "historico.html", headers=NO_CACHE)

    @app.get("/relatorios", include_in_schema=False)
    async def page_reports():
        return FileResponse(paths.WEB_DIR / "relatorios.html", headers=NO_CACHE)

    @app.get("/configuracoes", include_in_schema=False)
    async def page_settings():
        return FileResponse(paths.WEB_DIR / "configuracoes.html", headers=NO_CACHE)

    app.mount("/static", StaticFiles(directory=paths.STATIC_DIR), name="static")

    # ------------------------------------------------------ análise
    def _range(start: Optional[int], end: Optional[int]) -> tuple[int, int]:
        now = now_ms()
        end = end if end is not None else now
        start = start if start is not None else end - analytics.DAY
        if end <= start:
            raise HTTPException(400, "O fim do período precisa ser depois do início.")
        if end - start > builder.MAX_SPAN_DAYS * analytics.DAY:
            raise HTTPException(400, f"O período máximo é de {builder.MAX_SPAN_DAYS} dias.")
        return start, end

    @app.get("/api/analysis")
    async def api_analysis(start: Optional[int] = None, end: Optional[int] = None):
        s0, e0 = _range(start, end)
        return JSONResponse(analytics.analysis(db, store.settings, s0, e0), headers=NO_CACHE)

    @app.get("/api/timeline")
    async def api_timeline(start: Optional[int] = None, end: Optional[int] = None, types: str = "",
                           limit: int = Query(1000, ge=1, le=10000)):
        s0, e0 = _range(start, end)
        tlist = [t for t in types.split(",") if t] or None
        evs = db.list_events(tlist, s0, e0, limit, now_ms())
        return JSONResponse({"events": evs, "truncated": len(evs) >= limit}, headers=NO_CACHE)

    # ---------------------------------------------------- relatórios
    def _report(tipo: str, data: Optional[str], inicio: Optional[int], fim: Optional[int]) -> tuple[dict, str]:
        try:
            s0, e0, label = builder.resolve_period(tipo, data, inicio, fim)
        except builder.PeriodError as exc:
            raise HTTPException(400, str(exc))
        rep = builder.report_data(db, store.settings, engine.provider_info(), tipo, s0, e0, label,
                                  simulated=engine.simulated)
        return rep, builder.filename(tipo, s0, e0)

    @app.get("/relatorio", include_in_schema=False)
    async def report_view(tipo: str = "diario", data: Optional[str] = None, inicio: Optional[int] = None,
                          fim: Optional[int] = None):
        rep, _ = _report(tipo, data, inicio, fim)
        return HTMLResponse(builder.build_html(rep, "view"), headers=NO_CACHE)

    @app.get("/relatorio/baixar", include_in_schema=False)
    async def report_download(tipo: str = "diario", data: Optional[str] = None, inicio: Optional[int] = None,
                              fim: Optional[int] = None):
        rep, name = _report(tipo, data, inicio, fim)
        body = builder.build_html(rep, "file").encode("utf-8")
        return Response(body, media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', **NO_CACHE})

    # ------------------------------------------------- configurações
    @app.get("/api/settings")
    async def get_settings():
        return JSONResponse(engine.settings_info(), headers=NO_CACHE)

    @app.post("/api/settings")
    async def post_settings(request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON inválido")
        if not isinstance(body, dict):
            raise HTTPException(400, "formato inválido")
        return await engine.update_settings(body)

    # --------------------------------------------------------------- API
    @app.get("/api/health")
    async def health():
        return {"app": "monitor-de-internet", "version": __version__, "simulated": engine.simulated,
                "running": engine.started, "cycles": engine.cycle_count}

    @app.get("/api/state")
    async def state():
        return JSONResponse(engine.snapshot(), headers=NO_CACHE)

    @app.get("/api/samples")
    async def samples(since: int = Query(...), limit: int = Query(3600, ge=1, le=20000)):
        return JSONResponse(db.samples_since(since, limit), headers=NO_CACHE)

    @app.get("/api/events")
    async def events(types: str = "", limit: int = Query(100, ge=1, le=2000),
                     since: Optional[int] = None, until: Optional[int] = None):
        tlist = [t for t in types.split(",") if t] or None
        return JSONResponse(db.list_events(tlist, since, until, limit, now_ms()), headers=NO_CACHE)

    @app.get("/api/stats")
    async def stats():
        return JSONResponse(engine.refresh_stats(), headers=NO_CACHE)

    # ------------------------------------------------ antena Starlink
    @app.get("/api/dish")
    async def dish_state():
        return JSONResponse(engine.dish_api.public(), headers=NO_CACHE)

    @app.get("/api/dish/obstrucao")
    async def dish_map():
        try:
            return JSONResponse(await engine.dish_api.obstruction_map(), headers=NO_CACHE)
        except DishUnavailable as exc:
            raise HTTPException(503, f"Mapa de obstruções indisponível: {exc}")

    @app.get("/api/dish/quedas")
    async def dish_outages(start: Optional[int] = None, end: Optional[int] = None):
        s0, e0 = _range(start, end)
        return JSONResponse({"summary": db.dish_cause_summary(s0, e0), "stats": db.dish_stats_summary(s0, e0)},
                            headers=NO_CACHE)

    @app.post("/api/mode")
    async def set_mode(body: ModeBody):
        if body.mode not in CONNECTION_MODES:
            raise HTTPException(400, "Modo de conexão inválido")
        return await engine.set_mode(body.mode)

    @app.post("/api/provider")
    async def set_provider(body: ProviderBody):
        try:
            return await engine.set_provider(body.type, body.name.strip()[:80], body.equip_host,
                                             body.apply_thresholds)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/shutdown")
    async def shutdown(request: Request):
        if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "não permitido")
        log.info("Encerramento solicitado pelo painel/atalho")
        if request_shutdown is not None:
            asyncio.get_running_loop().call_later(0.3, request_shutdown)
        return {"ok": True, "message": "Encerrando o monitoramento…"}

    # --------------------------------------------------------- WebSocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host", "")
        if host not in allowed_hosts or (origin is not None and origin not in allowed_origins):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = hub.register()

        async def sender():
            while True:
                data = await queue.get()
                if data is None:
                    await websocket.close(code=1013)
                    return
                await websocket.send_text(data)

        send_task = asyncio.create_task(sender())
        try:
            while True:
                # o painel envia "ping" periodicamente; serve para detectar desconexão
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            hub.unregister(queue)
            send_task.cancel()

    return app
