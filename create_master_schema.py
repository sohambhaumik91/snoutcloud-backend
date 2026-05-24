#!/usr/bin/env python3
"""Combine OpenAPI + Pydantic models into single schema file."""

import json
from pathlib import Path

def main():
    # Load the individual schemas
    try:
        with open("openapi.json") as f:
            openapi = json.load(f)
    except FileNotFoundError:
        print("❌ openapi.json not found. Run: curl http://localhost:8000/openapi.json > openapi.json")
        return

    try:
        with open("schema_models.json") as f:
            models_data = json.load(f)
            models = models_data.get("models", {})
    except FileNotFoundError:
        print("❌ schema_models.json not found. Run: python export_schema.py")
        return

    # Combine
    master_schema = {
        "generated_at": "2026-04-13",
        "description": "Complete API schema for PawLog backend",
        "stack": {
            "framework": "FastAPI",
            "db": "Supabase (Postgres + pgvector)",
            "python_version": "3.12",
            "orm": "Pydantic v2"
        },
        "openapi": openapi,
        "pydantic_models": models,
    }

    output_file = Path("SCHEMA.json")
    with open(output_file, "w") as f:
        json.dump(master_schema, f, indent=2)

    print(f"✅ Master schema created: {output_file}")
    print(f"   Size: {output_file.stat().st_size / 1024:.1f} KB")
    print("\n📄 Files ready for next Claude instance:")
    print("   - SCHEMA.json (complete)")
    print("   - SCHEMA_REFERENCE.md (guide)")
    print("   - openapi.json (raw OpenAPI)")

if __name__ == "__main__":
    main()
