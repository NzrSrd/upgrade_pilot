"""Write the OpenAPI schema where `openapi-typescript` can read it.

A file rather than a live fetch: CI generates types without starting a
server, and the checked-in result is diffable, so a change to the HTTP
contract appears in a pull request instead of surfacing as a frontend type
error days later.

`create_app` registers routes synchronously and only opens SQLite and Chroma
inside its lifespan, so this touches no store and needs no environment.
"""

import json
from pathlib import Path

from upgradepilot.api.app import create_app

DESTINATION = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "openapi.json"
)


def main() -> None:
    schema = create_app().openapi()
    DESTINATION.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {DESTINATION} ({len(schema['components']['schemas'])} schemas)")


if __name__ == "__main__":
    main()
