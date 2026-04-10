import base64
import json
import anthropic
from app.core.config import settings
from app.core.models import DocType

_client: anthropic.Anthropic | None = None


def get_anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


CLASSIFY_AND_OCR_PROMPT = """
You are processing a document uploaded to a dog health tracking app.

Your job has two parts:
1. Extract ALL text from this document exactly as it appears (OCR)
2. Classify what type of document this is

Document types:
- prescription: vet prescription with drug names, dosages
- vaccine_certificate: vaccination record with vaccine names, dates
- lab_report: blood work, urinalysis, or other test results
- discharge_summary: hospital discharge or post-surgery notes
- observation_note: handwritten or typed owner notes about the dog
- travel_note: notes or photos about a trip with the dog
- excel_sheet: spreadsheet with medical schedule or logs
- grooming_record: grooming invoice or record
- unknown: cannot determine

Respond with ONLY this JSON — no preamble, no markdown:
{
  "ocr_text": "<full extracted text from the document>",
  "doc_type": "<one of the types above>",
  "doc_type_confidence": <0.0 to 1.0>,
  "doc_type_reasoning": "<one sentence why>"
}
"""


async def ocr_and_classify(
    file_bytes: bytes,
    content_type: str
) -> tuple[str, DocType, float]:
    """
    Run Claude Vision OCR on the file and classify its doc_type.
    Returns: (ocr_text, doc_type, confidence)
    """
    client = get_anthropic()

    # encode to base64 for the vision API
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    # map content_type to anthropic media type
    media_type_map = {
        "image/jpeg":       "image/jpeg",
        "image/png":        "image/png",
        "image/webp":       "image/webp",
        "application/pdf":  "application/pdf",
    }
    media_type = media_type_map.get(content_type, "image/jpeg")

    # PDFs use document source type; images use image source type
    if media_type == "application/pdf":
        source = {
            "type": "base64",
            "media_type": "application/pdf",
            "data": b64
        }
        content_type_key = "document"
    else:
        source = {
            "type": "base64",
            "media_type": media_type,
            "data": b64
        }
        content_type_key = "image"

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": content_type_key,
                        "source": source
                    },
                    {
                        "type": "text",
                        "text": CLASSIFY_AND_OCR_PROMPT
                    }
                ]
            }
        ]
    )

    raw = message.content[0].text.strip()

    # strip markdown fences if model added them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)

    doc_type_str = result.get("doc_type", "unknown")
    try:
        doc_type = DocType(doc_type_str)
    except ValueError:
        doc_type = DocType.UNKNOWN

    return (
        result.get("ocr_text", ""),
        doc_type,
        float(result.get("doc_type_confidence", 0.5))
    )
