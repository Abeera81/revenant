"""The pre-registered evaluation: TabPFN vs logistic regression vs base rate.

Two steps so numbers can be reported before any results file is written:
  python src/backtest.py           # fit, compute, print; raw results -> data/backtest_raw.json
  python src/backtest.py --write   # results/backtest.json, backtest.md, PNG curves

Threshold rule (fixed before fitting, MEMORY.md): maximise Youden's J on the
training split only. Training-split scores are 5-fold out-of-fold predictions
within the training split, so the threshold never sees the test split.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, confusion_matrix, roc_auc_score, roc_curve
from sklearn.calibration import calibration_curve
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURES, TABLE_PATH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT / "data" / "backtest_raw.json"
RESULTS = ROOT / "results"
SEED = 20261031
N_BOOT = 2000
ABLATED = "days_since_last_release"


def make_models():
    from tabpfn import TabPFNClassifier
    return {
        "tabpfn": lambda: TabPFNClassifier(random_state=SEED, device="cpu"),
        "logreg": lambda: make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000)),
        "base_rate": lambda: DummyClassifier(strategy="prior"),
    }


def fit_predict(factory, Xtr, ytr, Xte):
    m = factory()
    m.fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1]


def oof_train_scores(factory, Xtr, ytr):
    oof = np.zeros(len(ytr))
    for a, b in StratifiedKFold(5, shuffle=True, random_state=SEED).split(Xtr, ytr):
        oof[b] = fit_predict(factory, Xtr[a], ytr[a], Xtr[b])
    return oof


def youden_threshold(y, s):
    fpr, tpr, thr = roc_curve(y, s)
    i = int(np.argmax(tpr - fpr))
    return float(min(thr[i], 1.0 + 1e-9)), float(tpr[i] - fpr[i])


def top_decile_lift(y, s, rng):
    k = int(np.ceil(0.1 * len(y)))
    order = np.lexsort((rng.random(len(s)), -s))  # ties broken at random
    return float(y[order[:k]].mean() / y.mean())


def bootstrap(y, s, rng):
    aucs, lifts = [], []
    n = len(y)
    for _ in range(N_BOOT):
        idx = rng.integers(0, n, n)
        yb, sb = y[idx], s[idx]
        if yb.min() == yb.max():
            continue
        aucs.append(roc_auc_score(yb, sb))
        lifts.append(top_decile_lift(yb, sb, rng))
    ci = lambda a: [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]
    return ci(aucs), ci(lifts), len(aucs)


def evaluate(name, ytr, train_scores, yte, s, rng):
    thr, j = youden_threshold(ytr, train_scores)
    pred = (s >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(yte, pred, labels=[0, 1]).ravel()
    auc_ci, lift_ci, n_ok = bootstrap(yte, s, rng)
    frac_pos, mean_pred = calibration_curve(yte, s, n_bins=10, strategy="uniform")
    return {
        "roc_auc": float(roc_auc_score(yte, s)), "roc_auc_ci95": auc_ci,
        "top_decile_lift": top_decile_lift(yte, s, rng), "top_decile_lift_ci95": lift_ci,
        "bootstrap_resamples_used": n_ok,
        "brier": float(brier_score_loss(yte, s)),
        "threshold": thr, "train_youden_j": j,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "reliability": {"mean_predicted": mean_pred.tolist(), "fraction_dormant": frac_pos.tolist()},
    }


def run_condition(df, feats, models, tr, te, label):
    X = df[feats].to_numpy(float)
    y = df["dormant"].to_numpy(int)
    out, scores = {}, {}
    for name, factory in models.items():
        t0 = time.time()
        s = fit_predict(factory, X[tr], y[tr], X[te])
        oof = oof_train_scores(factory, X[tr], y[tr])
        out[name] = evaluate(name, y[tr], oof, y[te], s, np.random.default_rng(SEED))
        scores[name] = s.tolist()
        r = out[name]
        print(f"[{label}] {name:9s} AUC {r['roc_auc']:.4f} CI [{r['roc_auc_ci95'][0]:.4f}, {r['roc_auc_ci95'][1]:.4f}]  "
              f"lift@10% {r['top_decile_lift']:.3f} CI [{r['top_decile_lift_ci95'][0]:.3f}, {r['top_decile_lift_ci95'][1]:.3f}]  "
              f"Brier {r['brier']:.4f}  thr {r['threshold']:.4f}  CM {r['confusion_matrix']}  ({time.time() - t0:.0f}s)",
              flush=True)
    return out, scores


def paired_auc_diff(y, a, b, rng):
    """Exploratory, NOT pre-registered: paired bootstrap of AUC(a) - AUC(b)."""
    d = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() == y[idx].max():
            continue
        d.append(roc_auc_score(y[idx], a[idx]) - roc_auc_score(y[idx], b[idx]))
    return {"point": float(roc_auc_score(y, a) - roc_auc_score(y, b)),
            "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}


def compute():
    df = pd.read_parquet(TABLE_PATH)
    y = df["dormant"].to_numpy(int)
    tr, te = train_test_split(np.arange(len(df)), test_size=0.3, stratify=y, random_state=SEED)
    print(f"rows {len(df)}  train {len(tr)}  test {len(te)}  base rate all {y.mean():.4f}  "
          f"train {y[tr].mean():.4f}  test {y[te].mean():.4f}", flush=True)
    models = make_models()
    full, full_scores = run_condition(df, FEATURES, models, tr, te, "full")
    abl_feats = [f for f in FEATURES if f != ABLATED]
    ablation, _ = run_condition(df, abl_feats, models, tr, te, f"no {ABLATED}")

    yte = y[te]
    s_tab = np.array(full_scores["tabpfn"])
    diff = paired_auc_diff(yte, s_tab, np.array(full_scores["logreg"]), np.random.default_rng(SEED))
    print(f"exploratory paired AUC diff tabpfn-logreg {diff['point']:.4f} CI [{diff['ci95'][0]:.4f}, {diff['ci95'][1]:.4f}]")

    test = df.iloc[te].reset_index(drop=True).assign(p_tabpfn=s_tab)
    cols = ["name", "p_tabpfn", *FEATURES]
    fps = test[test.dormant == 0].nlargest(3, "p_tabpfn")[cols]
    fns = test[test.dormant == 1].nsmallest(3, "p_tabpfn")[cols]
    print("highest-confidence false positives (active in 2024, TabPFN said dormant):")
    print(fps.to_string(index=False))
    print("highest-confidence false negatives (went quiet in 2024, TabPFN said active):")
    print(fns.to_string(index=False))

    raw = {
        "n_rows": len(df), "n_train": len(tr), "n_test": len(te),
        "base_rate": {"all": float(y.mean()), "train": float(y[tr].mean()), "test": float(yte.mean())},
        "tabpfn": {"package_version": __import__("importlib.metadata").metadata.version("tabpfn"),
                   "model_version": __import__("tabpfn.model_loading").model_loading.resolve_model_version(None).value,
                   "device": "cpu"},
        "split": {"test_size": 0.3, "stratified": True, "random_state": SEED},
        "bootstrap_resamples": N_BOOT,
        "threshold_rule": "max Youden's J on 5-fold out-of-fold scores within the training split",
        "full": full, "ablation_without_" + ABLATED: ablation,
        "exploratory_not_preregistered": {"paired_auc_diff_tabpfn_minus_logreg": diff},
        "highest_confidence_false_positives": fps.to_dict("records"),
        "highest_confidence_false_negatives": fns.to_dict("records"),
        "test_scores": {"y": yte.tolist(), **full_scores},
    }
    RAW_PATH.write_text(json.dumps(raw, indent=2))
    print(f"raw results -> {RAW_PATH}")


COLORS = {"tabpfn": "#2a78d6", "logreg": "#eb6834", "base_rate": "#898781"}
LABELS = {"tabpfn": "TabPFN", "logreg": "Logistic regression", "base_rate": "Base rate"}


def write():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    raw = json.loads(RAW_PATH.read_text())
    sc = raw.pop("test_scores")
    y = np.array(sc["y"])
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "backtest.json").write_text(json.dumps(raw, indent=2))

    plt.rcParams.update({"axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                         "grid.color": "#e5e4df", "grid.linewidth": 0.8, "axes.edgecolor": "#898781"})
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], ls=":", color="#c3c2b7", lw=1)
    for m in ("tabpfn", "logreg", "base_rate"):
        fpr, tpr, _ = roc_curve(y, sc[m])
        ax.plot(fpr, tpr, lw=2, color=COLORS[m], label=f"{LABELS[m]} (AUC {raw['full'][m]['roc_auc']:.3f})")
    ax.set(xlabel="False positive rate", ylabel="True positive rate",
           title=f"ROC, held-out test split (n={raw['n_test']})")
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout(); fig.savefig(RESULTS / "roc.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], ls=":", color="#c3c2b7", lw=1, label="Perfect calibration")
    for m in ("tabpfn", "logreg", "base_rate"):
        r = raw["full"][m]["reliability"]
        ax.plot(r["mean_predicted"], r["fraction_dormant"], lw=2, marker="o", ms=6, color=COLORS[m],
                label=f"{LABELS[m]} (Brier {raw['full'][m]['brier']:.3f})")
    ax.set(xlabel="Mean predicted probability (10 uniform bins)", ylabel="Observed fraction dormant",
           title="Reliability, held-out test split", xlim=(0, 1), ylim=(0, 1))
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout(); fig.savefig(RESULTS / "reliability.png", dpi=150); plt.close(fig)

    def table(cond):
        lines = ["| Model | ROC AUC [95% CI] | Top-decile lift [95% CI] | Brier | Threshold | TN | FP | FN | TP |",
                 "|---|---|---|---|---|---|---|---|---|"]
        for m in ("tabpfn", "logreg", "base_rate"):
            r = raw[cond][m]; c = r["confusion_matrix"]
            lines.append(f"| {LABELS[m]} | {r['roc_auc']:.4f} [{r['roc_auc_ci95'][0]:.4f}, {r['roc_auc_ci95'][1]:.4f}] | "
                         f"{r['top_decile_lift']:.3f} [{r['top_decile_lift_ci95'][0]:.3f}, {r['top_decile_lift_ci95'][1]:.3f}] | "
                         f"{r['brier']:.4f} | {r['threshold']:.4f} | {c['tn']} | {c['fp']} | {c['fn']} | {c['tp']} |")
        return "\n".join(lines)

    def errs(rows):
        if not rows:
            return "(none)"
        keys = list(rows[0])
        out = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
        for r in rows:
            out.append("| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in r.values()) + " |")
        return "\n".join(out)

    b = raw["base_rate"]; d = raw["exploratory_not_preregistered"]["paired_auc_diff_tabpfn_minus_logreg"]
    md = f"""# Backtest results

Generated by `python src/backtest.py` then `python src/backtest.py --write`. All numbers come from that run; raw values are in `backtest.json`.

Rows {raw['n_rows']} (train {raw['n_train']}, test {raw['n_test']}), stratified 70/30, random_state={raw['split']['random_state']}.
Base rate (dormant): all {b['all']:.4f}, train {b['train']:.4f}, test {b['test']:.4f}.
CIs: percentile bootstrap over the test split, {raw['bootstrap_resamples']} resamples.
Threshold: {raw['threshold_rule']}.
The base-rate model gives every package the same score, so its top decile is a random tie-break (seeded) and its lift reflects only that draw; its threshold of 1.0 predicts nothing as dormant.

## All eleven features

{table('full')}

## Ablation: `days_since_last_release` removed

{table('ablation_without_' + ABLATED)}

## Exploratory (not pre-registered)

Paired bootstrap AUC difference, TabPFN minus logistic regression: {d['point']:.4f} [{d['ci95'][0]:.4f}, {d['ci95'][1]:.4f}].

## Highest-confidence false positives (published in 2024; TabPFN scored them most likely dormant)

{errs(raw['highest_confidence_false_positives'])}

## Highest-confidence false negatives (went quiet in 2024; TabPFN scored them least likely dormant)

{errs(raw['highest_confidence_false_negatives'])}

Curves: `roc.png`, `reliability.png`. TabPFN is used under its non-commercial licence.
"""
    (RESULTS / "backtest.md").write_text(md, encoding="utf-8")
    print("wrote results/backtest.json, backtest.md, roc.png, reliability.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    write() if a.write else compute()
