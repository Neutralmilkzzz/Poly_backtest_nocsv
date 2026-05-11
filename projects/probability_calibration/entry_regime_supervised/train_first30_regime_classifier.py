from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_DIR = ROOT / "projects" / "probability_calibration" / "entry_regime_supervised" / "artifacts"
DATASET_PATH = ARTIFACTS_DIR / "first30_regime_dataset.csv"
RESULTS_PATH = ARTIFACTS_DIR / "first30_model_results.json"

FEATURE_COLUMNS = [
    "opening_up_prob",
    "current_up_prob_30",
    "move_0_30",
    "abs_move_0_30",
    "slope_per_sec_0_30",
    "mean_prob_0_30",
    "distance_from_half_30",
    "er_0_30",
    "hurst_0_30",
    "path_length_0_30",
    "range_width_0_30",
    "crossing_count_0_30",
    "max_run_ratio_0_30",
    "realized_vol_0_30",
]

SPLIT_SIZES = {"train": 60, "validation": 20, "test": 20}


def load_dataset() -> pd.DataFrame:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATASET_PATH}")
    df = pd.read_csv(DATASET_PATH)
    expected = sum(SPLIT_SIZES.values())
    if len(df) < expected:
        raise ValueError(f"Need at least {expected} rows, found {len(df)}")
    return df.iloc[:expected].copy()


def split_dataset(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train_end = SPLIT_SIZES["train"]
    val_end = train_end + SPLIT_SIZES["validation"]
    train_df = df.iloc[:train_end].copy()
    val_df = df.iloc[train_end:val_end].copy()
    test_df = df.iloc[val_end:val_end + SPLIT_SIZES["test"]].copy()
    return train_df, val_df, test_df


def build_models() -> dict[str, Pipeline]:
    scaled_preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scaler", StandardScaler()),
                    ]
                ),
                FEATURE_COLUMNS,
            )
        ]
    )

    tree_preprocessor = ColumnTransformer(
        transformers=[
            ("numeric", SimpleImputer(strategy="median"), FEATURE_COLUMNS),
        ]
    )

    return {
        "logistic_regression": Pipeline(
            steps=[
                ("preprocess", scaled_preprocessor),
                ("model", LogisticRegression(max_iter=2000, random_state=42)),
            ]
        ),
        "decision_tree": Pipeline(
            steps=[
                ("preprocess", tree_preprocessor),
                ("model", DecisionTreeClassifier(max_depth=4, min_samples_leaf=4, random_state=42)),
            ]
        ),
        "random_forest": Pipeline(
            steps=[
                ("preprocess", tree_preprocessor),
                ("model", RandomForestClassifier(
                    n_estimators=300,
                    max_depth=5,
                    min_samples_leaf=3,
                    random_state=42,
                )),
            ]
        ),
    }


def evaluate_split(model: Pipeline, df: pd.DataFrame) -> dict[str, object]:
    x = df[FEATURE_COLUMNS]
    y = df["target_trend_dangerous"]
    prob = model.predict_proba(x)[:, 1]
    pred = (prob >= 0.5).astype(int)
    metrics = {
        "accuracy": accuracy_score(y, pred),
        "f1": f1_score(y, pred, zero_division=0),
        "roc_auc": roc_auc_score(y, prob) if y.nunique() > 1 else None,
        "confusion_matrix": confusion_matrix(y, pred).tolist(),
        "classification_report": classification_report(y, pred, zero_division=0, output_dict=True),
    }
    return metrics


def main() -> None:
    df = load_dataset()
    train_df, val_df, test_df = split_dataset(df)

    models = build_models()
    results: dict[str, object] = {
        "split_sizes": {k: int(v) for k, v in SPLIT_SIZES.items()},
        "feature_columns": FEATURE_COLUMNS,
        "models": {},
    }

    best_name = None
    best_f1 = -1.0
    fitted_models: dict[str, Pipeline] = {}

    for name, model in models.items():
        model.fit(train_df[FEATURE_COLUMNS], train_df["target_trend_dangerous"])
        fitted_models[name] = model
        val_metrics = evaluate_split(model, val_df)
        test_metrics = evaluate_split(model, test_df)
        results["models"][name] = {
            "validation": val_metrics,
            "test": test_metrics,
        }
        if val_metrics["f1"] > best_f1:
            best_f1 = float(val_metrics["f1"])
            best_name = name

    assert best_name is not None
    best_model = fitted_models[best_name]

    if hasattr(best_model.named_steps["model"], "feature_importances_"):
        importances = best_model.named_steps["model"].feature_importances_
        ranked_features = sorted(
            zip(FEATURE_COLUMNS, importances),
            key=lambda item: item[1],
            reverse=True,
        )
    else:
        coefs = best_model.named_steps["model"].coef_[0]
        ranked_features = sorted(
            zip(FEATURE_COLUMNS, [abs(v) for v in coefs]),
            key=lambda item: item[1],
            reverse=True,
        )

    results["best_model"] = {
        "name": best_name,
        "validation_f1": best_f1,
        "top_features": [{"feature": feature, "importance": float(score)} for feature, score in ranked_features[:8]],
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"Dataset rows used: {len(df)}")
    print(f"Split sizes: train={len(train_df)}, validation={len(val_df)}, test={len(test_df)}")
    print(f"Best model on validation: {best_name}")
    for name, metrics in results["models"].items():
        val_metrics = metrics["validation"]
        test_metrics = metrics["test"]
        print(
            f"{name}: "
            f"val_acc={val_metrics['accuracy']:.3f}, val_f1={val_metrics['f1']:.3f}, "
            f"test_acc={test_metrics['accuracy']:.3f}, test_f1={test_metrics['f1']:.3f}"
        )
    print("Top features:")
    for item in results["best_model"]["top_features"]:
        print(f"  {item['feature']}: {item['importance']:.4f}")
    print(f"Saved results to: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
