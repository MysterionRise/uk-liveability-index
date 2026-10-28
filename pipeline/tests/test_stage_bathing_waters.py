import pytest

from lix_pipeline.stage.bathing_waters import class_score


@pytest.mark.parametrize(
    ("text", "score"),
    [("Excellent", 4), ("Good Water Quality", 3), ("sufficient", 2), ("Poor", 1),
     ("un-assessed", None), (None, None), ("Closed", None)],
)  # fmt: skip
def test_class_words_across_publishers(text, score):
    assert class_score(text) == score
