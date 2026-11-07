"""Workstream C (Forecast): targets, features, model, backtest vs persistence baseline.

The output is a story's `article_count_7d` at each horizon in `config.HORIZONS`, with a range
(`contracts.Forecast`), i.e. the number the globe draws, so the globe can look into the future.

Must be point-in-time: anything computed "as of day t" may only use data <= t.
"""
