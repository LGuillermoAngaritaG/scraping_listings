"""Model search: tune each model type, add their average, keep the best on validation.

The validation rows come out of the training rows with the same per-city rule as the test
(rent_model.split). Tuning and choosing never see the test rows; train.py scores the winner
on them once, after. Kept apart from rent_model.py so scoring jobs do not need Optuna.
"""

import numpy as np
import optuna
from lightgbm import early_stopping

from rent_model import FAMILIES, FEATURES, TARGET, Blend, build_model, metrics, split

ROUNDS = 3000  # most trees a model may grow; early stopping keeps fewer
PATIENCE = 100  # stop once the validation error has not improved for this many trees


def lightgbm_space(trial) -> dict:
    return {
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 8, 128, log=True),
        "min_child_samples": trial.suggest_int("min_child_samples", 5, 100, log=True),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.4, 1.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10, log=True),
        "objective": trial.suggest_categorical("objective", ["regression", "huber", "l1"]),
    }


def catboost_space(trial) -> dict:
    return {
        "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
        "depth": trial.suggest_int("depth", 4, 9),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 0.5, 20, log=True),
        "loss_function": trial.suggest_categorical("loss_function", ["RMSE", "MAE", "Huber:delta=0.5"]),
    }


def xgboost_space(trial) -> dict:
    return {
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
        "max_depth": trial.suggest_int("max_depth", 3, 10),
        "min_child_weight": trial.suggest_float("min_child_weight", 1, 50, log=True),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.4, 1.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10, log=True),
        # No pseudo-Huber: on log prices it sometimes diverged to infinite prices (Nov 2026).
        "objective": trial.suggest_categorical("objective", ["reg:squarederror", "reg:absoluteerror"]),
    }


SPACES = {"lightgbm": lightgbm_space, "catboost": catboost_space, "xgboost": xgboost_space}


def fit_early(family: str, params: dict, fit, valid, seed: int):
    """Fit on `fit` with early stopping on `valid`. Returns (log price of valid, trees kept)."""
    pipeline = build_model(family, {**params, "n_estimators": ROUNDS}, seed).regressor
    encode, model = pipeline[:-1].fit(fit[FEATURES]), pipeline[-1]
    x, x_valid = encode.transform(fit[FEATURES]), encode.transform(valid[FEATURES])
    y, y_valid = np.log(fit[TARGET].to_numpy()), np.log(valid[TARGET].to_numpy())
    if family == "lightgbm":
        model.fit(x, y, eval_set=[(x_valid, y_valid)], callbacks=[early_stopping(PATIENCE, verbose=False)])
        rounds = model.best_iteration_
    elif family == "xgboost":
        model.set_params(early_stopping_rounds=PATIENCE).fit(x, y, eval_set=[(x_valid, y_valid)], verbose=False)
        rounds = model.best_iteration + 1
    else:
        model.fit(x, y, eval_set=(x_valid, y_valid), early_stopping_rounds=PATIENCE)
        rounds = model.get_best_iteration() + 1
    return model.predict(x_valid), rounds


def tune(family: str, fit, valid, trials: int, seed: int = 42):
    """Optuna's TPE sampler tries `trials` settings, each one steered by how the earlier ones did.

    Returns (best, trials table). best has the validation MAPE, the settings with the number of
    trees early stopping kept, and the log prices it gave the validation rows.
    """
    best = {}

    def objective(trial):
        params = SPACES[family](trial)
        log_price, rounds = fit_early(family, params, fit, valid, seed)
        score = metrics(valid[TARGET], np.exp(log_price))["mape"]
        trial.set_user_attr("trees", rounds)
        if not best or score < best["mape"]:
            best.update(mape=score, params={**params, "n_estimators": rounds}, log_price=log_price)
        return score

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=trials)
    return best, study.trials_dataframe()


def search(train, test_days: int, trials: int, seed: int = 42):
    """Tune each model type, add their average, and fit the winner on all of `train`.

    Returns (model, report). The report has the validation rows, the validation MAPE of each
    candidate, the winner, and the settings and trials table of each model type.
    """
    fit, valid, _, _ = split(train, test_days, seed)
    tuned = {family: tune(family, fit, valid, trials, seed) for family in FAMILIES}
    scores = {family: best["mape"] for family, (best, _) in tuned.items()}
    average = np.mean([best["log_price"] for best, _ in tuned.values()], axis=0)
    scores["blend"] = metrics(valid[TARGET], np.exp(average))["mape"]
    winner = min(scores, key=scores.get)

    grow = len(train) / len(fit)  # more rows to learn from, so a few more trees

    def final(family):
        params = tuned[family][0]["params"]
        return build_model(family, {**params, "n_estimators": max(50, round(params["n_estimators"] * grow))}, seed)

    model = Blend([final(f) for f in FAMILIES]) if winner == "blend" else final(winner)
    model.fit(train[FEATURES], train[TARGET])
    report = {
        "winner": winner,
        "valid_rows": len(valid),
        "valid_mape": scores,
        "params": {family: best["params"] for family, (best, _) in tuned.items()},
        "trials": {family: table for family, (_, table) in tuned.items()},
    }
    return model, report
