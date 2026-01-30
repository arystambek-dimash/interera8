from pydantic import BaseModel


class IntereraResponse(BaseModel):
    image_bytes: bytes
