from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_session
from app.dependencies.auth import require_admin
from app.services.layers import LAYER_DEFINITIONS
from app.services.translations import get_translation_request, list_translation_requests

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse("/admin", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    settings: Settings = Depends(get_settings),
):
    if username == settings.admin_username and password == settings.admin_password:
        request.session["admin_authenticated"] = True
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "بيانات الدخول غير صحيحة"}, status_code=401)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    requests = await list_translation_requests(session)
    return templates.TemplateResponse("dashboard.html", {"request": request, "requests": requests})


@router.get("/admin/requests/{request_id}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def request_detail(request_id: int, request: Request, session: AsyncSession = Depends(get_session)):
    translation_request = await get_translation_request(session, request_id)
    if translation_request is None:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    layer_by_position = {layer.position: layer for layer in translation_request.layers}
    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "item": translation_request,
            "layer_definitions": LAYER_DEFINITIONS,
            "layer_by_position": layer_by_position,
        },
    )


@router.get("/admin/requests/{request_id}/status", dependencies=[Depends(require_admin)])
async def request_status(request_id: int, session: AsyncSession = Depends(get_session)):
    translation_request = await get_translation_request(session, request_id)
    if translation_request is None:
        return {"found": False}

    layer_by_position = {layer.position: layer for layer in translation_request.layers}
    layers = []
    for definition in LAYER_DEFINITIONS:
        layer = layer_by_position.get(definition.position)
        layers.append(
            {
                "position": definition.position,
                "name": definition.name,
                "status": layer.status if layer else "pending",
                "model": layer.model if layer else "-",
                "duration_ms": layer.duration_ms if layer else None,
                "input_summary": layer.input_summary if layer else "-",
                "output_text": (layer.output_text or layer.error) if layer else "لم تبدأ هذه الطبقة بعد.",
            }
        )

    return {
        "found": True,
        "id": translation_request.id,
        "status": translation_request.status,
        "error": translation_request.error,
        "final_translation": translation_request.final_translation,
        "completed_layers": sum(1 for layer in translation_request.layers if layer.status == "completed"),
        "layers": layers,
    }
