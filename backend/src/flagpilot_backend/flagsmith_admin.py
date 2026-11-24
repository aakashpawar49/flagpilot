"""Async client for the Flagsmith Admin (management) API.

Endpoint shapes follow the Flagsmith source (api/experimentation/*, api/features/*).
Experimentation endpoints are beta upstream, so errors are surfaced verbatim.
"""

from typing import Any

import httpx


class FlagsmithError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: Any) -> None:
        super().__init__(f"Flagsmith {method} {path} -> {status}: {body}")
        self.status = status
        self.body = body


def _results(payload: Any) -> list[dict[str, Any]]:
    """Unwrap paginated ({results: [...]}) or plain list responses."""
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload or []


class FlagsmithAdmin:
    def __init__(
        self,
        *,
        api_url: str,
        app_url: str,
        admin_key: str,
        project_id: int | None,
        environment_key: str,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.app_url = app_url.rstrip("/")
        self.project_id = project_id
        self.environment_key = environment_key
        self._http = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Api-Key {admin_key}", "Accept": "application/json"},
            timeout=30.0,
        )
        self._environment: dict[str, Any] | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = resp.text[:500]
            raise FlagsmithError(method, path, resp.status_code, body)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    @property
    def _env(self) -> str:
        return f"/environments/{self.environment_key}"

    # --- Discovery -----------------------------------------------------------

    async def list_organisations(self) -> list[dict[str, Any]]:
        return _results(await self._req("GET", "/organisations/"))

    async def list_projects(self, organisation_id: int) -> list[dict[str, Any]]:
        return _results(await self._req("GET", f"/organisations/{organisation_id}/projects/"))

    async def list_environments(self, project_id: int) -> list[dict[str, Any]]:
        return _results(await self._req("GET", "/environments/", params={"project": project_id}))

    async def get_environment(self) -> dict[str, Any]:
        if self._environment is None:
            self._environment = await self._req("GET", f"{self._env}/")
            if self.project_id is None:
                self.project_id = self._environment["project"]
        return self._environment

    async def list_features(self, search: str | None = None) -> list[dict[str, Any]]:
        env = await self.get_environment()
        params: dict[str, Any] = {"environment": env["id"], "page_size": 100}
        if search:
            params["search"] = search
        return _results(await self._req("GET", f"/projects/{self.project_id}/features/", params=params))

    async def list_metrics(self) -> list[dict[str, Any]]:
        return _results(await self._req("GET", f"{self._env}/experiment-metrics/", params={"page_size": 100}))

    async def list_experiments(self) -> list[dict[str, Any]]:
        return _results(await self._req("GET", f"{self._env}/experiments/", params={"page_size": 100}))

    async def list_warehouse_connections(self) -> list[dict[str, Any]]:
        return _results(await self._req("GET", f"{self._env}/warehouse-connections/"))

    # --- Provisioning ----------------------------------------------------------

    async def create_multivariate_feature(self, name: str, description: str, control_value: str) -> dict[str, Any]:
        await self.get_environment()
        return await self._req(
            "POST",
            f"/projects/{self.project_id}/features/",
            json={
                "name": name,
                "type": "MULTIVARIATE",
                "description": description,
                "initial_value": control_value,
                "default_enabled": True,
            },
        )

    async def create_variant_option(self, feature_id: int, key: str, value: str) -> dict[str, Any]:
        # Allocation stays 0 outside the experiment: only enrolled identities see variants.
        return await self._req(
            "POST",
            f"/projects/{self.project_id}/features/{feature_id}/mv-options/",
            json={
                "feature": feature_id,
                "type": "unicode",
                "string_value": value,
                "default_percentage_allocation": 0,
                "key": key,
            },
        )

    async def create_metric(
        self, *, name: str, description: str, event: str, aggregation: str, direction: str
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"{self._env}/experiment-metrics/",
            json={
                "name": name,
                "description": description,
                "aggregation": aggregation,
                "direction": direction,
                "definition": {"version": 1, "event": event},
            },
        )

    async def create_experiment(
        self,
        *,
        feature_id: int,
        name: str,
        hypothesis: str,
        metric_id: int,
        expected_direction: str,
        control_value: str,
        option_weights: dict[int, float],
        rollout_percentage: float,
    ) -> dict[str, Any]:
        """Creates the experiment in status `created`. Nothing is served until `start`."""
        return await self._req(
            "POST",
            f"{self._env}/experiments/",
            json={
                "feature": feature_id,
                "name": name,
                "hypothesis": hypothesis,
                "metrics": [{"metric": metric_id, "expected_direction": expected_direction}],
                "experiment_rollout": {
                    "enabled": True,
                    "rollout_percentage": rollout_percentage,
                    "feature_state_value": {"type": "string", "value": control_value},
                    "multivariate_feature_state_values": [
                        {"multivariate_feature_option": option_id, "percentage_allocation": weight}
                        for option_id, weight in option_weights.items()
                    ],
                },
            },
        )

    # --- Lifecycle -------------------------------------------------------------

    async def get_experiment(self, experiment_id: int) -> dict[str, Any]:
        return await self._req("GET", f"{self._env}/experiments/{experiment_id}/")

    async def start_experiment(self, experiment_id: int) -> dict[str, Any]:
        return await self._req("POST", f"{self._env}/experiments/{experiment_id}/start/")

    async def refresh_results(self, experiment_id: int) -> bool:
        """Asks Flagsmith to recompute results. Returns False when throttled (5 min floor)."""
        try:
            await self._req("POST", f"{self._env}/experiments/{experiment_id}/results/refresh/")
            await self._req("POST", f"{self._env}/experiments/{experiment_id}/exposures/refresh/")
            return True
        except FlagsmithError as exc:
            if exc.status in (400, 429):
                return False
            raise

    async def get_results(self, experiment_id: int) -> dict[str, Any] | None:
        return (await self._req("GET", f"{self._env}/experiments/{experiment_id}/results/"))["results"]

    async def get_exposures(self, experiment_id: int) -> dict[str, Any] | None:
        return (await self._req("GET", f"{self._env}/experiments/{experiment_id}/exposures/"))["exposures"]

    async def complete_experiment(
        self, experiment_id: int, decision: str, variant_option_id: int | None = None
    ) -> dict[str, Any]:
        """decision: 'control' | 'variant' | 'disable'. The flag then serves that value."""
        body: dict[str, Any] = {"rollout_decision": decision}
        if decision == "variant":
            body["rollout_variant"] = variant_option_id
        return await self._req("POST", f"{self._env}/experiments/{experiment_id}/complete/", json=body)

    async def create_server_key(self, name: str) -> str:
        data = await self._req("POST", f"{self._env}/api-keys/", json={"name": name})
        return data["key"]

    # --- Links -----------------------------------------------------------------

    async def experiment_url(self, experiment_id: int) -> str:
        env = await self.get_environment()
        return f"{self.app_url}/project/{self.project_id}/environment/{env['api_key']}/experiments/{experiment_id}"
