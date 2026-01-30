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
You are an industrial designer creating clean product-documentation drawings from a photo.

TASK SUMMARY:
From the input photo, identify EXACTLY ONE target object (usually furniture) and produce a set of 5 consistent product views:
1) front view, 2) right-side view, 3) back view, 4) left-side view, 5) top view.

TARGET OBJECT SELECTION (critical):
The user may provide EITHER:
A) A single red point marking the target object
B) No point, plus an optional text detail describing what object to select

INPUTS:
- Photo: provided by user
- Optional user detail request: "{optional_detail}" (may be empty)

RULE 1 — If a red point exists:
- The object UNDER the red point is the ONE AND ONLY target object.
- If the point is on/near an edge, choose the object that the point most clearly belongs to.
- Ignore all other objects completely.

RULE 2 — If no red point exists:
- If optional_detail is provided, use it to choose the target object from the photo.
  Examples: "the chair", "the white sofa", "the table near the window".
- If optional_detail is empty OR multiple objects match, choose using this priority:
  (1) the most central large furniture-like object
  (2) the largest clearly visible object
  (3) the object with the clearest complete silhouette
- If the photo contains no furniture-like object, select the most salient product-like object.

STRICT PRESERVATION (must hold):
- Preserve the target object identity: overall shape, proportions, structure, color(s), pattern, and material appearance.
- Do NOT redesign, do NOT add/remove parts, do NOT change style, do NOT “improve” it.
- Minor cleanup is allowed only to create a clean documentation drawing (e.g., removing noise, straightening lines).
- If the optional_detail conflicts with preservation, IGNORE the conflicting parts.

BACKGROUND REMOVAL:
- Remove the entire environment/background.
- Output must contain ONLY the isolated target object on a plain white background (or transparent if supported).
- No room elements, no floor, no shadows from the room, no other objects.

DOCUMENTATION DRAWING STYLE:
- Clean, crisp, product-documentation look.
- Consistent perspective across all 5 views (orthographic/isometric-like but consistent and readable).
- Uniform lighting; no dramatic shadows; no reflections from the room.
- Edges and contours should be clear.
- Keep textures/patterns minimal but faithful (don’t invent patterns).

VIEW CONSISTENCY RULES:
- All 5 views must represent the SAME object with matching colors and details.
- Dimensions/proportions must match across views.
- Do not mirror incorrectly: left view and right view must be correct.
- The top view must match the object’s real top surface and outline.

WHAT TO DO IF THE TARGET IS PARTIALLY OCCLUDED:
- Reconstruct the hidden parts conservatively using symmetry/common geometry ONLY when necessary.
- Never invent decorative details.
- Prefer the simplest plausible completion that keeps identity consistent.

OUTPUT REQUIREMENTS:
- Provide exactly 5 views: front, right, back, left, top.
- The target object is centered, large enough, and not cropped in any view.
- No extra annotations, no text labels, no measurements unless explicitly requested.

USER DETAIL REQUEST:
- User request (optional): {optional_detail}
- Apply it ONLY if it helps select the correct target object or clarifies non-identity details.
- Do NOT apply it if it changes the furniture identity (shape/material/color/pattern/structure).

FINAL QUALITY CHECK (must pass):
- Exactly ONE target object is depicted.
- The object looks like the same object from the photo (same shape + colors/material).
- No background environment is visible.
- All 5 views are present, correctly ordered, and consistent.
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
        image: UploadFile = File(...),
        optional_detail: str = Form(""),
        gemini_service: GeminiService = Depends(Provide[AppContainer.gemini_service]),
) -> Response:
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
