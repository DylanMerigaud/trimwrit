from trimwrit import verdict


def test_equal_data_is_a_coin_flip():
    post = verdict.posterior(2, 3, 2, 3)
    assert abs(post["p_helps"] - 0.5) < 1e-9


def test_posterior_means_are_laplace_smoothed():
    post = verdict.posterior(3, 3, 0, 4)
    assert abs(post["with_mean"] - 4 / 5) < 1e-12
    assert abs(post["without_mean"] - 1 / 6) < 1e-12


def test_equivalence_is_symmetric_in_the_arms():
    a = verdict.posterior(5, 9, 2, 7)
    b = verdict.posterior(2, 7, 5, 9)
    assert abs(a["p_equivalent"] - b["p_equivalent"]) < 1e-9
    assert abs(a["p_helps"] + b["p_helps"] - 1.0) < 1e-9


def test_three_runs_never_decide():
    out = verdict.decide(3, 3, 0, 3)
    assert out["verdict"] == verdict.UNDECIDED
    assert 0.98 < out["p_helps"] < 0.99


def test_six_clean_runs_keep():
    out = verdict.decide(6, 6, 0, 6)
    assert out["verdict"] == verdict.KEEP
    assert out["p_helps"] >= 0.99


def test_a_rule_that_makes_it_worse_is_harmful():
    assert verdict.decide(0, 6, 6, 6)["verdict"] == verdict.HARMFUL


def test_eighteen_perfect_runs_per_arm_are_inert_fifteen_are_not_yet():
    out = verdict.decide(18, 18, 18, 18)
    assert out["verdict"] == verdict.INERT
    assert out["without_rate"] == 1.0
    assert verdict.decide(15, 15, 15, 15)["verdict"] == verdict.UNDECIDED


def test_equivalent_but_failing_without_the_rule_is_inert_fail():
    assert verdict.decide(0, 18, 0, 18)["verdict"] == verdict.INERT_FAILING


def test_a_small_noisy_gap_is_undecided():
    assert verdict.decide(2, 3, 1, 3)["verdict"] == verdict.UNDECIDED


def test_an_arm_with_no_measured_run_is_undecided():
    out = verdict.decide(0, 0, 3, 3)
    assert out["verdict"] == verdict.UNDECIDED
    assert out["with_rate"] is None
    assert out["without_rate"] == 1.0
    assert out["passes"] == {"with": 0, "without": 3}
    assert out["measured"] == {"with": 0, "without": 3}


def test_the_lines_are_parameters():
    assert verdict.decide(3, 3, 0, 3, helps_line=0.95)["verdict"] == verdict.KEEP


def test_no_effect_rarely_decides_under_repeated_looks():
    shares = verdict.simulate(0.5, 0.5)["shares"]
    assert shares.get(verdict.KEEP, 0) <= 0.08
    assert shares.get(verdict.HARMFUL, 0) <= 0.08


def test_a_big_effect_is_found():
    shares = verdict.simulate(1.0, 1 / 3, reps=100)["shares"]
    assert shares.get(verdict.KEEP, 0) >= 0.95


def test_a_perfect_inert_rule_is_proven_after_eighteen_runs():
    out = verdict.simulate(1.0, 1.0, reps=50)
    assert out["shares"].get(verdict.INERT, 0) == 1.0
    assert out["mean_runs"] == 18


def test_an_effect_of_exactly_the_margin_is_rarely_called_equivalent():
    shares = verdict.simulate(1.0, 0.8)["shares"]
    assert shares.get(verdict.INERT, 0) + shares.get(verdict.INERT_FAILING, 0) <= 0.12


def test_the_grid_is_pinned_numerically():
    assert abs(verdict.posterior(3, 3, 0, 3)["p_helps"] - 0.98571) < 1e-4
    assert abs(verdict.posterior(18, 18, 18, 18)["p_equivalent"] - 0.98684) < 1e-4
    assert abs(verdict.posterior(2, 3, 1, 3)["p_helps"] - 0.75714) < 1e-4


def test_counts_that_are_not_counts_are_refused():
    for args in [(5, 3, 0, 3), (0, 3, 5, 3), (-1, 3, 0, 3), (0, 3, 0, -1), (0, -2, 0, 3)]:
        for fn in (verdict.decide, verdict.posterior):
            try:
                fn(*args)
            except ValueError:
                continue
            raise AssertionError("%s%r did not raise" % (fn.__name__, args))


def test_the_last_partial_batch_runs():
    out = verdict.simulate(0.5, 0.5, reps=5, batch=4, max_runs=30)
    assert out["mean_runs"] == 30
