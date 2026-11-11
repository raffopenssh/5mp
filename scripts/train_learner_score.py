#!/usr/bin/env python3
"""Fit the learner's approval score and write srv/learned_score_model.go.

    go run ./cmd/movement-eval -replay-only -dump-learner /tmp/learner.csv
    python3 scripts/train_learner_score.py /tmp/learner.csv

One row per learned feature per day of the autofetch archive, written by the
harness from the SAME Go code the server decides with (srv.LearnerFeatures),
so training and serving cannot drift. label = the feature was used again
within the horizon after that day.

Per family (airfield / place / road) a logistic regression is fitted on the
`train` days only, with SIGN CONSTRAINTS: more evidence may never lower the
score and more idle time may never raise it (terms in FREE are unconstrained).
The unconstrained fit gave "seen in both directions" and "more journeys"
negative weights -- collinearity with the day count, not aviation -- and a
score a reviewer cannot read is not evidence. A term the data does not support
ends at 0 and is printed as such. Class weights are NOT balanced: the output
is used as a probability.

The raw logit is then recalibrated (Platt, two parameters) on the `test` days,
because the training window is too short to see long idle times.

Thresholds are chosen on the `test` days (later in time, one horizon of
embargo after the last training day):
  approve  lowest score such that precision >= TARGET at it AND at every
           higher score (so approving more confident features is never worse)
  reject   highest score such that, among features idle >= MIN_IDLE active
           days, at most 1-TARGET are used again
A family with no threshold meeting the target gets approve = 2: nothing is
auto-approved, and the generated file says so.
"""
import sys
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

TARGET = 0.95         # what the replay must show for the approved STATE
# The threshold is chosen for a stricter precision than the target: an approved
# feature keeps its status for learnedDemoteMinIdle active days after its score
# dips, and those grace days cost precision the threshold has to pay for.
THRESH_TARGET = 0.975
MIN_SUPPORT = 30      # feature-days above a threshold before its precision is believed
MIN_IDLE = 14         # srv.learnedRejectMinIdle
C = 1.0
FREE = {"ln_length_m"}  # no expected direction
OUT = "srv/learned_score_model.go"
NAMES = {  # must equal srv.LearnerFeatureNames (TestLearnedScoreModelMatchesFeatures)
    "airfield": ["ln_days", "ln_journeys", "ln_trackers", "both_directions", "axis_agreement", "known_airfield", "ln_idle_active_days"],
    "place": ["ln_days", "ln_trackers", "ln_events", "ln_dwell_median", "near_osm_place", "on_known_road", "ln_idle_active_days"],
    "road": ["ln_days", "ln_trackers", "ln_events", "ln_length_m", "connected_ends", "ln_idle_active_days"],
}


def main(path):
    df = pd.read_csv(path, keep_default_na=False)
    out = []
    for fam, names in NAMES.items():
        d = df[df.family == fam]
        X = np.array([[float(v) for v in s.split(";")] for s in d.features]) if len(d) else np.zeros((0, len(names)))
        y = d.label.to_numpy()
        tr, te = (d.set == "train").to_numpy(), (d.set == "test").to_numpy()
        print(f"\n{fam}: {tr.sum()} train / {te.sum()} test feature-days, positives {y[tr].mean() if tr.sum() else 0:.2f} / {y[te].mean() if te.sum() else 0:.2f}")
        if tr.sum() < 50 or len(set(y[tr])) < 2 or te.sum() < MIN_SUPPORT:
            print("  not enough data: no model, nothing auto-approved")
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0)
        sd[sd == 0] = 1
        Z, yt = (X[tr] - mu) / sd, y[tr]

        def nll(w):
            z = Z @ w[1:] + w[0]
            return np.logaddexp(0, z).sum() - (yt * z).sum() + 0.5 / C * (w[1:] ** 2).sum()

        def grad(w):
            r = 1 / (1 + np.exp(-(Z @ w[1:] + w[0]))) - yt
            return np.concatenate([[r.sum()], Z.T @ r + w[1:] / C])

        bounds = [(None, None)] + [(None, None) if n in FREE else ((None, 0) if n.startswith("ln_idle") else (0, None)) for n in names]
        w = minimize(nll, np.zeros(len(names) + 1), jac=grad, bounds=bounds, method="L-BFGS-B").x
        coef = w[1:] / sd
        icpt = w[0] - (coef * mu).sum()
        z_all = X @ coef + icpt
        cal = LogisticRegression(C=1e6, max_iter=1000).fit(z_all[te].reshape(-1, 1), y[te])
        pa, pb = float(cal.coef_[0][0]), float(cal.intercept_[0])
        if pa <= 0:
            pa, pb = 1.0, 0.0
        p = 1 / (1 + np.exp(-(pa * z_all + pb)))
        print(f"  Platt on held-out: a={pa:.3f} b={pb:.3f}")
        if len(set(y[te])) == 2:
            print(f"  test AUC {roc_auc_score(y[te], p[te]):.3f}  Brier {((p[te]-y[te])**2).mean():.3f}")
        for n, c in zip(names, coef):
            print(f"    {n:22s} {c:+.3f}")
        # approve: scan from the top
        order = np.argsort(-p[te])
        ps, ys = p[te][order], y[te][order]
        cum = np.cumsum(ys) / np.arange(1, len(ys) + 1)
        approve, ok = 2.0, True
        for i in range(len(ps)):
            if i + 1 < MIN_SUPPORT:
                continue
            if i + 1 < len(ps) and ps[i + 1] == ps[i]:
                continue  # a threshold cannot split equal scores
            if cum[i] >= THRESH_TARGET and ok:
                approve = float(ps[i])
            else:
                ok = False
        if approve <= 1:
            k = ps >= approve
            print(f"  approve >= {approve:.3f}: precision {ys[k].mean():.3f}, recall {ys[k].sum()/max(ys.sum(),1):.3f} ({k.sum()} of {len(ps)} feature-days)")
        else:
            print(f"  NO threshold reaches {THRESH_TARGET:.1%} precision with >= {MIN_SUPPORT} feature-days: nothing auto-approved")
        idle = d.idle_active_days.to_numpy()[te] >= MIN_IDLE
        reject = -1.0
        if idle.sum() >= MIN_SUPPORT:
            pi, yi = p[te][idle], y[te][idle]
            o = np.argsort(pi)
            pi, yi = pi[o], yi[o]
            c = np.cumsum(yi) / np.arange(1, len(yi) + 1)
            for i in range(MIN_SUPPORT - 1, len(pi)):
                if i + 1 < len(pi) and pi[i + 1] == pi[i]:
                    continue
                if c[i] <= 1 - THRESH_TARGET:
                    reject = float(pi[i])
                else:
                    break
            if reject >= 0:
                k = pi <= reject
                print(f"  reject <= {reject:.3f} (idle >= {MIN_IDLE}): {k.sum()} feature-days, {yi[k].mean():.3f} used again")
        if reject < 0:
            print("  no reject threshold: nothing auto-rejected")
        out.append((fam, names, icpt, coef, approve, reject, pa, pb, int(tr.sum()), int(te.sum())))

    with open(OUT, "w") as f:
        f.write("// Code generated by scripts/train_learner_score.py; DO NOT EDIT.\n//\n")
        f.write("// P(a learned feature is used again within LearnerHorizonDays), per family.\n")
        f.write("// Fitted on the first half of the autofetch archive, thresholds chosen on the\n")
        f.write(f"// later half for >= {TARGET:.0%} precision (cmd/movement-eval, LEARNER REPLAY).\n")
        f.write("// Approve = 2 means no threshold met the target: nothing is auto-approved.\n\n")
        f.write("package srv\n\nvar learnedScoreModels = map[string]learnedScoreModelT{\n")
        for fam, names, icpt, coef, approve, reject, pa, pb, ntr, nte in out:
            f.write(f'\t"{fam}": {{ // {ntr} train / {nte} test feature-days\n')
            f.write("\t\tFeatures:  []string{" + ", ".join(f'"{n}"' for n in names) + "},\n")
            f.write(f"\t\tIntercept: {icpt:.6f},\n")
            f.write("\t\tCoef:      []float64{" + ", ".join(f"{c:.6f}" for c in coef) + "},\n")
            f.write(f"\t\tApprove:   {approve:.6f},\n\t\tReject:    {reject:.6f},\n\t\tPlattA:    {pa:.6f},\n\t\tPlattB:    {pb:.6f},\n\t}},\n")
        f.write("}\n")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main(sys.argv[1])
