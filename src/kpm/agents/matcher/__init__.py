"""Matcher agent: pairs Kalshi and Polymarket contracts on the same NFL outcome.

Stage 1 (rules.py): deterministic matching on teams, date, kickoff -> confidence score.
Stage 2 (review.py + backends.py): AI review of full titles/descriptions/rules -> JSON.
Stage 3 (pipeline.py): gate; anything flagged or < threshold goes to a manual-review CSV.
"""
