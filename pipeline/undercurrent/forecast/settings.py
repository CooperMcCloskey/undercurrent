"""Knobs for the forecast workstream (C).

Constants that other workstreams also need (HORIZONS, FORECAST_INTERVAL) stay in config.py.
Everything here can be overridden from the command line (python -m undercurrent.forecast.run).
"""

from dataclasses import dataclass

# A story gets a forecast only if it had at least this many articles in the last 7 days. Below
# that the counts are mostly noise, and the log-ratio target is dominated by rounding.
MIN_ARTICLES_7D = 20

# Rows from the first days of a panel are dropped: the growth and acceleration features look
# 14 days back, and before the panel starts every story would look new.
BURN_IN_DAYS = 21

# Wikipedia pageviews are published about a day late, so a forecast made on day t may only use
# views up to t - 1. Edits are known straight away.
WIKI_VIEWS_LAG_DAYS = 1
WIKI_EDITS_LAG_DAYS = 0

# Walk-forward evaluation (see backtest.py).
FINAL_TEST_DAYS = 90  # the fixed, never-tuned-on test period at the end of the data
FOLD_DAYS = 91  # earlier test windows (one quarter each), going backwards
N_FOLDS = 3  # how many earlier windows to add (0 = only the final test period)
CALIBRATION_DAYS = 28  # block just before each test window, used to widen/narrow the ranges
MIN_TRAIN_DAYS = 120  # a fold needs at least this many days of training rows
MIN_TRAIN_ROWS = 500  # ...and a horizon needs at least this many training rows,
MIN_CAL_ROWS = 100  # ...and this many calibration rows, or it is skipped (no forecast)

# Explanations ("factors") are only computed for the latest days: they cost one extra prediction
# per feature per row.
FACTOR_DAYS = 14
TOP_FACTORS = 3

# Stories are processed in chunks so the (stories x days) matrices stay small.
STORY_CHUNK = 5000


# Neural model: how many days of each story's history it reads (the window ending at day t).
SEQ_DAYS = 28


@dataclass(frozen=True)
class NNParams:
    """Settings for the neural model (neural.py). Like HGBParams: tune on the earlier folds,
    never on the final test period."""

    hidden: int = 64
    dropout: float = 0.1
    epochs: int = 15
    batch_size: int = 512
    learning_rate: float = 2e-3
    weight_decay: float = 1e-4
    patience: int = 3  # stop after this many epochs without a better validation loss
    val_fraction: float = 0.1  # the most recent share of training DAYS, kept for early stopping
    seeds: int = 1  # train this many networks with different seeds and average them
    pretrain_epochs: int = 5  # epochs on the auxiliary data (if given) before fine-tuning
    pretrain_learning_rate: float = 1e-3
    device: str = "auto"  # "auto" = cuda, then mps, then cpu


@dataclass(frozen=True)
class HGBParams:
    """Gradient boosting settings. Tuned by the backtest, not by hand: change them on the
    earlier folds, never on the final test period."""

    max_iter: int = 200
    learning_rate: float = 0.06
    max_leaf_nodes: int = 31
    min_samples_leaf: int = 100
    l2_regularization: float = 1.0
