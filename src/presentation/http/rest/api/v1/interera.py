from __future__ import annotations

import base64
import imghdr
import textwrap
import uuid
from pathlib import Path
from typing import List

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from starlette.requests import Request
from starlette.responses import Response

from app.di import AppContainer
from src.domain.entity import Media
from src.infrastructure.integrations.gemini_service import GeminiService
from src.presentation.http.rest.api.v1.schemas.interera import IntereraResponse

router = APIRouter()

temp_db: dict[str, List[bytes]] = {}

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_HISTORY = 10

INTERERA_PROMPT = textwrap.dedent("""
Furnish and decorate this empty interior space in a realistic and elegant way.
Keep the exact same room size, proportions, camera angle, perspective, walls, ceiling, windows, doors, and lighting direction.
Do not change the layout, structure, or architecture.
Only add stylish furniture, decor, textiles, and lighting fixtures that fit naturally into the existing space.

Design style: modern, cozy, high-end interior design.
Realistic materials, natural colors, soft shadows, photorealistic quality.

Do NOT do:
- distortion, warping
- extra walls, new windows/doors, new rooms
- layout/architecture changes
- altered room size, changed perspective/camera angle
- fisheye / wide angle distortion
""").strip()

INPAINT_PROMPT_TEMPLATE = textwrap.dedent("""
You will receive TWO images:
- Image 1: a room photo containing a furniture object.
- Image 2: a binary mask (same size). White = the target object. Black = everything else.

TASK:
1) Identify the furniture object in Image 1 that corresponds to the WHITE area of Image 2.
2) Using ONLY that object as reference, generate a professional furniture design drawing sheet.
3) The output must be ONE clean sheet on a pure white (or very light neutral) background.

VIEWS (same scale):
- Front orthographic
- Side orthographic
- Back orthographic
- Top orthographic
- One 3/4 perspective

STRICT:
- Preserve silhouette, proportions, legs, cushions, seams, panels, materials, and colors.
- No redesign, no style changes, no extra features.
- No room/background. Only the isolated object on white.
- Clean industrial design / product documentation look.

User note: {optional_detail}
""").strip()


def _get_session_id(request: Request) -> str | None:
    return request.cookies.get("session")


def _ensure_session_id(request: Request, response: Response) -> str:
    session_id = _get_session_id(request)
    if session_id:
        return session_id

    new_id = uuid.uuid4().hex
    response.set_cookie(
        key="session",
        value=new_id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 7,
    )
    return new_id


def _detect_media_type(data: bytes) -> str:
    kind = imghdr.what(None, h=data)
    if kind == "png":
        return "image/png"
    if kind in ("jpeg", "jpg"):
        return "image/jpeg"
    if kind == "webp":
        return "image/webp"
    return "application/octet-stream"


def _append_to_session_cache(session_id: str, img_bytes: bytes) -> None:
    history = temp_db.setdefault(session_id, [])
    history.append(img_bytes)

    if len(history) > MAX_HISTORY:
        del history[:-MAX_HISTORY]


async def _read_upload(u: UploadFile, fallback_mime: str | None = None) -> tuple[bytes, str]:
    mime = u.content_type or fallback_mime or ""
    if mime not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content_type={mime!r}. Allowed: {sorted(ALLOWED_IMAGE_TYPES)}",
        )
    data = await u.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")
    return data, mime


async def _run_gemini(
        gemini_service: GeminiService,
        prompt: str,
        uploads: list[UploadFile],
        debug_names: list[str] | None = None,
) -> bytes:
    medias: list[Media] = []
    debug_names = debug_names or [f"img{i + 1}" for i in range(len(uploads))]
    DEBUG_DIR = Path(__file__).parent / "debug"
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    for u, name in zip(uploads, debug_names):
        fallback = "image/png" if name == "mask" else None
        data, mime = await _read_upload(u, fallback_mime=fallback)
        medias.append(Media(media_data=data, media_type=mime))
        with open(f"{DEBUG_DIR}/{name}.bin", "wb") as f:
            f.write(data)
    img = await gemini_service.execute(prompt.strip(), medias=medias)
    if not img:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Gemini returned no image.")
    return img


def _require_existing_session(request: Request) -> str:
    session_id = _get_session_id(request)
    print(session_id)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session cookie.")
    return session_id


@router.post("/interera")
@inject
async def create_interera(
        request: Request,
        image: UploadFile = File(...),
        gemini_service: GeminiService = Depends(Provide[AppContainer.gemini_service]),
) -> Response:
    img_bytes = await _run_gemini(gemini_service, INTERERA_PROMPT, [image])

    session_id = _get_session_id(request) or uuid.uuid4().hex
    _append_to_session_cache(session_id, img_bytes)

    media_type = _detect_media_type(img_bytes)
    resp = Response(content=img_bytes, media_type=media_type)

    if not _get_session_id(request):
        resp.set_cookie(
            key="session",
            value=session_id,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=60 * 60 * 24 * 7,
        )

    return resp


@router.post("/interera/inpaint", response_model=IntereraResponse)
@inject
async def create_interera_inpaint(
        request: Request,
        image: UploadFile = File(...),  # base image (current generated)
        mask: UploadFile = File(...),  # mask (black/white)
        optional_detail: str = Form(""),
        gemini_service: GeminiService = Depends(Provide[AppContainer.gemini_service]),
) -> Response:
    prompt = INPAINT_PROMPT_TEMPLATE.format(optional_detail=optional_detail.strip())

    img_bytes = await _run_gemini(gemini_service, prompt, [image, mask])

    session_id = _get_session_id(request) or uuid.uuid4().hex
    _append_to_session_cache(session_id, img_bytes)

    resp = Response(content=img_bytes, media_type=_detect_media_type(img_bytes))
    if not _get_session_id(request):
        resp.set_cookie(
            key="session",
            value=session_id,
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=60 * 60 * 24 * 7,
        )
    return resp


@router.get("/interera/history")
async def get_interera_history(request: Request):
    session_id = _require_existing_session(request)

    history = temp_db.get(session_id)
    if not history:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No generated images for this session.")

    return {
        "count": len(history),
        "images_base64": [base64.b64encode(b).decode("utf-8") for b in history],
    }
