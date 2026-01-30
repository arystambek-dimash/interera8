from io import BytesIO

from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.domain.entity import Media

load_dotenv()


class GeminiService:
    def __init__(self, api_key: str, model_id: str = "gemini-2.5-flash-image"):
        self.client = genai.Client(api_key=api_key)
        self.model_id = model_id

    async def execute(self, prompt: str, media: Media = None) -> bytes | None:
        contents = [prompt]

        if media:
            contents.append(types.Part.from_bytes(
                data=media.media_data,
                mime_type=media.media_type
            ))

        response = await self.client.aio.models.generate_content(
            model=self.model_id,
            contents=contents
        )
        image = None
        for part in response.parts:
            if part.text is not None:
                print(part.text)
            elif part.inline_data is not None:
                image = part.as_image()

        out_pil = Image.open(BytesIO(image.image_bytes)).convert("RGB")
        out_buf = BytesIO()
        out_pil.save(out_buf, format="PNG", optimize=True)
        return out_buf.getvalue()