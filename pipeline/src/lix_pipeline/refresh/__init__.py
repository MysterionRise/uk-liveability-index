"""``lix refresh``: bring the built data up to date with the least work.

``plan`` decides what is stale from the lockfile and the sidecars (pure functions);
``run`` resolves, fetches, stages, rebuilds and validates in that order, recording a
sidecar after each step so an interrupted run resumes and a finished one is a no-op.
"""
