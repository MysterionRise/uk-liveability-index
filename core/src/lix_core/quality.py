"""Quality levels of an indicator value, shared by the pipeline, the API and the browser.

The level is a string in ``long.parquet`` and ``lsoa_features.parquet``; ``scores.parquet``
stores its index as a uint8 (``q__{indicator}`` columns) and the manifest lists the
levels in order, so no reader hard-codes them.
"""

QUALITY_LEVELS: tuple[str, ...] = (
    "ok",
    "imputed",  # estimated from another source (Greater Manchester crime from the IoD)
    "low_n",  # few observations behind the value
    "broadcast_msoa",  # copied down from the MSOA
    "broadcast_lad",  # copied down from the local authority
    "missing",  # the source should cover this area but has no value
    "not_available",  # the indicator is not built for this nation
    "capped",  # most homes have none within the search distance: the value is that distance
)
QUALITY_CODE = {level: i for i, level in enumerate(QUALITY_LEVELS)}
