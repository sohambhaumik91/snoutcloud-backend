#!/usr/bin/env python3
"""Export API schema for documentation."""

import json
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.models import *

def get_all_models():
    """Extract all Pydantic models from app.core.models."""
    import app.core.models as models_module

    model_schemas = {}
    for name in dir(models_module):
        obj = getattr(models_module, name)
        # Check if it's a Pydantic model
        if hasattr(obj, 'model_json_schema'):
            try:
                model_schemas[name] = obj.model_json_schema()
            except Exception as e:
                print(f"⚠️  Skipped {name}: {e}")

    return model_schemas

def main():
    print("📋 Exporting Pydantic models...")
    models = get_all_models()

    schema = {
        "generated_at": "2026-04-13",
        "type": "pydantic_models",
        "models": models,
        "count": len(models),
    }

    output_file = Path("schema_models.json")
    with open(output_file, "w") as f:
        json.dump(schema, f, indent=2)

    print(f"✅ Exported {len(models)} models to {output_file}")
    print("\nModels:")
    for name in sorted(models.keys()):
        print(f"  - {name}")

if __name__ == "__main__":
    main()
