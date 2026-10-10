"""Every source in the registry is read by something, and the manifest credits the backbone."""

from lix_core.config import load_indicators, load_registry
from lix_pipeline.stage import BACKBONE_INPUTS, STAGE_INPUTS, stagers


def _consumed() -> set[str]:
    catalogue = load_indicators()
    return (
        set(stagers())
        | {s for inputs in STAGE_INPUTS.values() for s in inputs}
        | {s for i in catalogue.indicators for s in i.sources}
        | set(BACKBONE_INPUTS)
    )


def test_every_p0_source_is_consumed():
    registry = load_registry()
    consumed = _consumed()
    idle = [s for s, spec in registry.items() if spec.priority == "P0" and s not in consumed]
    assert idle == [], f"P0 sources nothing reads: {idle}"


def test_lower_priority_idle_sources_say_why():
    registry = load_registry()
    consumed = _consumed()
    unexplained = [s for s, spec in registry.items() if s not in consumed and not spec.notes]
    assert unexplained == [], f"idle sources without a note: {unexplained}"


def test_indicator_sources_exist():
    registry = load_registry()
    missing = {
        i.id: [s for s in i.sources if s not in registry]
        for i in load_indicators().indicators
        if any(s not in registry for s in i.sources)
    }
    assert missing == {}


def test_backbone_is_in_the_registry():
    registry = load_registry()
    assert all(s in registry for s in BACKBONE_INPUTS), BACKBONE_INPUTS
