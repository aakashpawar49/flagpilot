import json

import httpx
import respx

from flagpilot_backend.flagsmith_admin import FlagsmithAdmin, FlagsmithError

API = "https://api.flagsmith.test/api/v1"
ENV = "envKey123"


def client() -> FlagsmithAdmin:
    return FlagsmithAdmin(api_url=API, app_url="https://app.test", admin_key="k", project_id=7, environment_key=ENV)


@respx.mock
async def test_create_experiment_payload_and_auth():
    route = respx.post(f"{API}/environments/{ENV}/experiments/").mock(
        return_value=httpx.Response(201, json={"id": 42, "status": "created"})
    )
    fs = client()
    out = await fs.create_experiment(
        feature_id=1,
        name="Short signup",
        hypothesis="h",
        metric_id=9,
        expected_direction="increase",
        control_value="control",
        option_weights={11: 50.0},
        rollout_percentage=100,
    )
    assert out["id"] == 42
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Api-Key k"
    body = json.loads(req.content)
    assert body["metrics"] == [{"metric": 9, "expected_direction": "increase"}]
    rollout = body["experiment_rollout"]
    assert rollout["feature_state_value"] == {"type": "string", "value": "control"}
    assert rollout["multivariate_feature_state_values"] == [
        {"multivariate_feature_option": 11, "percentage_allocation": 50.0}
    ]


@respx.mock
async def test_variant_option_keyed_with_zero_default_allocation():
    route = respx.post(f"{API}/projects/7/features/5/mv-options/").mock(
        return_value=httpx.Response(201, json={"id": 3})
    )
    await client().create_variant_option(5, "short_form", "short_form")
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "feature": 5,
        "type": "unicode",
        "string_value": "short_form",
        "default_percentage_allocation": 0,
        "key": "short_form",
    }


@respx.mock
async def test_complete_with_variant_decision():
    route = respx.post(f"{API}/environments/{ENV}/experiments/42/complete/").mock(
        return_value=httpx.Response(200, json={"id": 42})
    )
    await client().complete_experiment(42, "variant", 11)
    assert json.loads(route.calls.last.request.content) == {"rollout_decision": "variant", "rollout_variant": 11}


@respx.mock
async def test_refresh_throttled_returns_false():
    respx.post(f"{API}/environments/{ENV}/experiments/42/results/refresh/").mock(
        return_value=httpx.Response(429, json={"detail": "A refresh was requested recently."})
    )
    assert await client().refresh_results(42) is False


@respx.mock
async def test_errors_carry_status_and_body():
    respx.get(f"{API}/environments/{ENV}/").mock(return_value=httpx.Response(403, json={"detail": "nope"}))
    try:
        await client().get_environment()
    except FlagsmithError as exc:
        assert exc.status == 403
        assert exc.body == {"detail": "nope"}
    else:
        raise AssertionError("expected FlagsmithError")
