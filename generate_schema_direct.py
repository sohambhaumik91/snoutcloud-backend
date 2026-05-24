#!/usr/bin/env python3
"""Generate API schema directly from code without running server."""

import json
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

def main():
    print("[*] Generating schema from code...")

    # Import models
    import app.core.models as models_module

    # Get all Pydantic models
    model_schemas = {}
    for name in dir(models_module):
        obj = getattr(models_module, name)
        if hasattr(obj, 'model_json_schema'):
            try:
                model_schemas[name] = obj.model_json_schema()
                print(f"  + {name}")
            except Exception as e:
                print(f"  - {name}: {e}")

    # Import app to get routes
    from app.main import app

    # Extract routes
    routes = []
    for route in app.routes:
        if hasattr(route, 'path') and hasattr(route, 'methods'):
            routes.append({
                "path": route.path,
                "methods": list(route.methods),
                "name": getattr(route, 'name', 'unnamed'),
            })

    # Build output
    schema_models = {
        "generated_at": "2026-04-13",
        "type": "pydantic_models",
        "models": model_schemas,
        "count": len(model_schemas),
    }

    schema_routes = {
        "generated_at": "2026-04-13",
        "type": "fastapi_routes",
        "routes": routes,
        "count": len(routes),
    }

    # Save files
    with open("schema_models.json", "w") as f:
        json.dump(schema_models, f, indent=2)
    print(f"\n[OK] Saved schema_models.json ({len(model_schemas)} models)")

    with open("schema_routes.json", "w") as f:
        json.dump(schema_routes, f, indent=2)
    print(f"[OK] Saved schema_routes.json ({len(routes)} routes)")

    # Get OpenAPI schema from app
    openapi_schema = app.openapi()
    with open("openapi.json", "w") as f:
        json.dump(openapi_schema, f, indent=2)
    print(f"[OK] Saved openapi.json")

    # Combine into master schema
    master = {
        "generated_at": "2026-04-13",
        "description": "Complete API schema for PawLog backend",
        "stack": {
            "framework": "FastAPI",
            "db": "Supabase (Postgres + pgvector)",
            "python": "3.12",
            "pydantic": "v2"
        },
        "models": model_schemas,
        "routes": routes,
        "openapi": openapi_schema,
    }

    with open("SCHEMA.json", "w") as f:
        json.dump(master, f, indent=2)
    print(f"[OK] Saved SCHEMA.json (master schema)")

    print("\n[DONE] Schema generation complete!")
    print("\nFiles created:")
    print("  - SCHEMA.json ............. master schema (for next Claude)")
    print("  - openapi.json ............ OpenAPI spec")
    print("  - schema_models.json ...... Pydantic model schemas")
    print("  - schema_routes.json ...... FastAPI routes")
    print("  - SCHEMA_REFERENCE.md .... human-readable guide")

if __name__ == "__main__":
    main()
