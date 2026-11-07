"""Workstream B (Stories): turn articles and posts into stable, sensible stories.

    cluster.py   cluster one batch of items (FAISS + components + Louvain); scales to a real day
    tracker.py   the day-by-day state: match items to stories, start new ones, merge, retire
    track.py     the command that runs the tracker over a range of days, resumably
    graph.py     the small prototype that cluster.py grew from (and its place rule, used by both)

Reads the vector store and A's articles; writes data/processed/story_assignments/, stories_daily/
and story_lineage/. See docs/WORKPLAN.md sections 3.5 and 3.6.
"""
