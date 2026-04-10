import json
import anthropic
from app.core.config import settings
from app.core.models import DocType, ExtractionPayload

_client: anthropic.Anthropic | None = None


def get_anthropic() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


# ------------------------------------------------------------------
# Per-doc-type extraction prompts
# Each prompt tells the LLM exactly what entities to extract
# and what the data blob should contain.
# ------------------------------------------------------------------

BASE_INSTRUCTIONS = """
You are extracting structured data from a dog health document for a pet memory app.

Extract entities and return ONLY valid JSON — no preamble, no markdown fences.

For temporal fields, extract whatever precision is available:
- If you see "March 12, 2024" → year: 2024, month: 3, day: 12, precision: "day"
- If you see "March 2024"     → year: 2024, month: 3, day: null, precision: "month"
- If you see "2024"           → year: 2024, month: null, day: null, precision: "year"
- If approximate ("last month", "recently") → precision: "approximate"
- If unknown                  → precision: "unknown"

Always generate a semantic_summary — one sentence describing what this document contains.
This is used as the primary retrieval target when users search for documents.

If critical information is missing (e.g. date unclear, drug name illegible),
add a clarification_question asking the owner.
Maximum 3 clarification questions.
"""

PROMPTS: dict[DocType, str] = {

    DocType.PRESCRIPTION: BASE_INSTRUCTIONS + """
Document type: PRESCRIPTION

Extract one entity per drug prescribed. Each entity has:
- entity_type: "prescription"
- entity_subtype: null
- temporal: when the prescription was written
- location_name: clinic name if visible
- data: {
    "drug_name": normalised lowercase drug name,
    "brand_name": brand if visible,
    "dosage_mg": numeric value only,
    "dosage_unit": "mg" | "ml" | "tablet",
    "frequency": "SID" | "BID" | "TID" | "QID" | "PRN" | "as needed",
    "duration_days": integer,
    "route": "oral" | "topical" | "SC" | "IV" | "IM" | "ophthalmic" | "otic",
    "instructions": verbatim instruction text,
    "vet_name": vet name if visible,
    "clinic_name": clinic name if visible,
    "diagnosed_for": condition being treated if mentioned,
    "symptoms_noted": list of symptoms mentioned
  }

Also extract any episode_hint — a suggested label like "Gastroenteritis March 2024"
if the document clearly describes a clinical episode.

Return:
{
  "doc_type": "prescription",
  "doc_type_confidence": <float>,
  "semantic_summary": "<one sentence>",
  "episode_hint": "<label or null>",
  "entities": [ ...one per drug... ],
  "clarification_questions": [ ...max 3... ]
}
""",

    DocType.VACCINE_CERTIFICATE: BASE_INSTRUCTIONS + """
Document type: VACCINE CERTIFICATE

Extract one entity per vaccine administered. Each entity has:
- entity_type: "vaccine"
- temporal: date administered
- location_name: clinic name if visible
- data: {
    "vaccine_name": "DHPPiL" | "Rabies" | "Leptospira" | etc,
    "manufacturer": manufacturer name,
    "batch_no": batch or lot number,
    "next_due": next due date as ISO string if visible,
    "vet_name": vet name,
    "clinic_name": clinic name,
    "certificate_no": certificate number if visible
  }

Return:
{
  "doc_type": "vaccine_certificate",
  "doc_type_confidence": <float>,
  "semantic_summary": "<one sentence>",
  "episode_hint": null,
  "entities": [ ...one per vaccine... ],
  "clarification_questions": [ ...max 3... ]
}
""",

    DocType.LAB_REPORT: BASE_INSTRUCTIONS + """
Document type: LAB REPORT

Extract one entity per test parameter (e.g. WBC, ALT, creatinine).
Group them by test_name in entity_subtype.

Each entity:
- entity_type: "lab_result"
- entity_subtype: "CBC" | "biochemistry" | "urinalysis" | "cytology" | "other"
- temporal: date sample collected or report date
- data: {
    "test_name": "CBC" | "liver panel" | "urinalysis" etc,
    "parameter": parameter name e.g. "WBC" "ALT" "creatinine",
    "value": numeric value,
    "unit": unit string,
    "reference_low": lower bound of normal range,
    "reference_high": upper bound of normal range,
    "flag": "normal" | "low" | "high" | "critical",
    "lab_name": lab or clinic name,
    "vet_name": requesting vet name
  }

Return:
{
  "doc_type": "lab_report",
  "doc_type_confidence": <float>,
  "semantic_summary": "<one sentence>",
  "episode_hint": "<label or null>",
  "entities": [ ...one per parameter... ],
  "clarification_questions": [ ...max 3... ]
}
""",

    DocType.OBSERVATION_NOTE: BASE_INSTRUCTIONS + """
Document type: OBSERVATION NOTE (owner-written)

Extract one entity for the full observation. There is no vet, no clinic.

- entity_type: "observation"
- entity_subtype: "symptom" | "behaviour" | "diet" | "general"
- temporal: when the observation was made (infer from note if possible)
- data: {
    "observations": [
      { "symptom": "...", "detail": "...", "severity": "mild|moderate|severe|unknown" }
    ],
    "raw_note": verbatim note text,
    "requires_vet": true if symptoms seem medically significant
  }

Return:
{
  "doc_type": "observation_note",
  "doc_type_confidence": <float>,
  "semantic_summary": "<one sentence>",
  "episode_hint": null,
  "entities": [ ...usually one... ],
  "clarification_questions": [ ...max 3... ]
}
""",

    DocType.TRAVEL_NOTE: BASE_INSTRUCTIONS + """
Document type: TRAVEL NOTE

Extract one travel event entity.

- entity_type: "travel_event"
- entity_subtype: "road_trip" | "flight" | "day_outing" | "stay"
- period_start / period_end if date range is visible
- temporal: departure date if known
- location_name: destination name
- data: {
    "destination": place name,
    "transport": "car" | "flight" | "train" | "unknown",
    "first_time": true if note says "first time",
    "notes": any other context from the note
  }

Return:
{
  "doc_type": "travel_note",
  "doc_type_confidence": <float>,
  "semantic_summary": "<one sentence>",
  "episode_hint": null,
  "entities": [ ...one... ],
  "clarification_questions": [ ...max 3... ]
}
""",
}

# fallback for unknown/unhandled doc types
FALLBACK_PROMPT = BASE_INSTRUCTIONS + """
Document type: UNKNOWN

Extract whatever structured information you can find.
Use entity_type: "unknown" and put everything in the data blob.

Return:
{
  "doc_type": "unknown",
  "doc_type_confidence": <float>,
  "semantic_summary": "<one sentence>",
  "episode_hint": null,
  "entities": [
    {
      "entity_type": "unknown",
      "data": { "raw_text": "<everything extracted>" },
      "confidence": <float>
    }
  ],
  "clarification_questions": [ ...max 3... ]
}
"""


async def extract_entities(
    ocr_text: str,
    doc_type: DocType
) -> ExtractionPayload:
    """
    Run the extraction LLM pass on OCR text.
    Returns a structured ExtractionPayload.
    """
    client = get_anthropic()
    prompt = PROMPTS.get(doc_type, FALLBACK_PROMPT)

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": f"{prompt}\n\n---\nDOCUMENT TEXT:\n{ocr_text}"
            }
        ]
    )

    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)
    return ExtractionPayload(**data)
