"""Write the OpenAPI schema to openapi.json.

`openapi.json` is a build artifact, not a committed source -- `npm run types`
produces it and immediately consumes it, and the *generated* client
(`frontend/src/lib/api.d.ts`) is what is committed and what the frontend compiles
against. Committing both a source and its derivative only invites them to
disagree.

Deliberately does not need a running application: the schema comes from
`create_app()` without starting its lifespan, so this works with no database, no
NATS and no containers. That is what lets type generation and a CI drift check
run anywhere.

The keys are sorted. The live `/schema/openapi.json` serves routes in
registration order, so generating from the server instead would produce the same
types in a different order and churn the committed file for no reason. This is
the one canonical path.

    cd frontend && npm run types    # exports, then regenerates the client
"""

from __future__ import annotations

import json
from pathlib import Path

from nats_lens.app import create_app


def main() -> None:
    spec = create_app().openapi_schema.to_schema()
    out = Path(__file__).resolve().parents[1] / "openapi.json"
    out.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    print(f"{out.name}: {len(spec['paths'])} paths, {len(spec['components']['schemas'])} schemas")


if __name__ == "__main__":
    main()
