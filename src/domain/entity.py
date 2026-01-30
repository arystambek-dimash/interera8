from dataclasses import dataclass


@dataclass
class Video:
    id: int | None
    data: bytes


@dataclass
class Media:
    media_data: bytes
    media_type: str
