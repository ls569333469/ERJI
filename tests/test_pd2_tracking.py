from engine.pd2.tracking import advance


def test_first_target_and_invalidation_are_monotonic():
    target = {"target_reached": {"t1": True, "daily": False, "weekly": False}}
    first = advance({}, target, "structure_confirmed", "0.9", 100, True)
    assert first["target_first_observed_at"] == {"t1": 100}
    previous = {"tracking": first, "phase": "structure_confirmed", "opportunity_plan": target}
    second = advance(previous, {"target_reached": {"t1": False}}, "invalidated", "1.0", 200, True)
    assert second["target_first_observed_at"] == {"t1": 100}
    assert second["first_invalidation_observed_at"] == 200
    assert second["protection_history"] == [{"price": "0.9", "observed_at": 100}, {"price": "1.0", "observed_at": 200}]
    third = advance({"tracking": second, "phase": "invalidated"}, {}, "invalidated", "0.8", 300, False)
    assert third == second


def test_stale_price_or_prior_hit_does_not_invent_first_observation():
    target = {"target_reached": {"t1": True}}
    assert advance({}, target, "early_trigger", "1", 100, False)["target_first_observed_at"] == {}
    prior = {"opportunity_plan": target}
    assert advance(prior, target, "early_trigger", "1", 200, True)["target_first_observed_at"] == {}
