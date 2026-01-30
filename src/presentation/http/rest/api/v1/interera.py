from __future__ import annotations

import base64
import imghdr
import textwrap
import uuid
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
ROLE:
You are an industrial designer creating product-documentation drawings.
...
USER DETAIL REQUEST:
- User request (optional): {optional_detail}
- Apply it ONLY if it does NOT change the furniture identity (shape/material/color).
  If it conflicts with STRICT PRESERVATION, ignore the request.

FINAL QUALITY CHECK (must pass):
- The furniture looks like the same object from the photo (same shape + colors).
- No background environment is visible.
- All 5 views are present and consistent.
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


async def _run_gemini(gemini_service: GeminiService, prompt: str, image: UploadFile) -> bytes:
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content_type={image.content_type}. Allowed: {sorted(ALLOWED_IMAGE_TYPES)}",
        )

    data = await image.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file.")

    media = Media(media_data=data, media_type=image.content_type)
    img = await gemini_service.execute(prompt.strip(), media=media)
    print(type(img))
    return img


def _require_existing_session(request: Request) -> str:
    session_id = _get_session_id(request)
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
    img_bytes = await _run_gemini(gemini_service, INTERERA_PROMPT, image)

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
        response: Response,
        image: UploadFile = File(...),
        optional_detail: str = Form(""),
        gemini_service: GeminiService = Depends(Provide[AppContainer.gemini_service]),
) -> IntereraResponse:
    prompt = INPAINT_PROMPT_TEMPLATE.format(optional_detail=optional_detail.strip())
    img_bytes = await _run_gemini(gemini_service, prompt, image)

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
