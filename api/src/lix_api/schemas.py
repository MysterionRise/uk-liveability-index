"""Export the API's models as JSON Schema for the front end's generated TypeScript types.

    uv run python -m lix_api.schemas      # writes contracts/schemas.json

CI regenerates it and fails if the committed file is stale.
"""

import json

from pydantic import TypeAdapter

from lix_api import models
from lix_api.agent.state import LiveabilityState
from lix_core.paths import get_project_root

EXPORTED = [
    models.Place, models.AreaProfile, models.RankResult, models.Comparison,
    models.PoiResult, models.Explanation, models.IndicatorInfo, models.WeightProfile,
    LiveabilityState,
]  # fmt: skip


def build() -> dict:
    defs: dict = {}
    for model in EXPORTED:
        schema = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": defs}


def main() -> None:
    path = get_project_root() / "contracts" / "schemas.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {path.relative_to(get_project_root())}")


if __name__ == "__main__":
    main()
