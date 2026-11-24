"""Simulated visitors for demos (allowed by the hackathon rules, and labelled as such).

Each visitor is a Flagsmith identity. Reading the flag through get_experiment_flag
records the exposure; converting visitors send the metric event. Flagsmith then
computes results exactly as it would for real traffic.
"""

import random
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from flagsmith import Flagsmith
from flagsmith.analytics import EventProcessorConfig


def simulate_traffic(
    *,
    server_key: str,
    sdk_api_url: str | None,
    flag_name: str,
    event: str,
    visitors: int,
    rates: dict[str, float],
    tag: str,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, dict[str, int]]:
    kwargs: dict = {"environment_key": server_key, "enable_events": True}
    if sdk_api_url:
        kwargs["api_url"] = sdk_api_url
        kwargs["event_processor_config"] = EventProcessorConfig(events_api_url=sdk_api_url)
    flagsmith = Flagsmith(**kwargs)

    exposures: Counter[str] = Counter()
    conversions: Counter[str] = Counter()
    lock = threading.Lock()
    done = 0

    def visit(i: int) -> None:
        nonlocal done
        identifier = f"sim-{tag}-{i}"
        flag = flagsmith.get_experiment_flag(flag_name, identifier=identifier)
        variant = flag.variant or (flag.value if isinstance(flag.value, str) else None) or "control"
        converted = random.random() < rates.get(variant, rates.get("control", 0.1))
        if converted:
            flagsmith.track_event(event, identifier=identifier)
        with lock:
            exposures[variant] += 1
            conversions[variant] += int(converted)
            done += 1
            if on_progress and done % 50 == 0:
                on_progress(done)

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(visit, range(visitors)))
    finally:
        flagsmith.close(timeout_seconds=30)

    return {key: {"visitors": exposures[key], "conversions": conversions[key]} for key in exposures}
