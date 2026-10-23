"""Prometheus metrics for DDLnzb bridge."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

bridge_http_requests_total = Counter(
    "ddlnzb_bridge_http_requests_total",
    "Bridge HTTP requests",
    ["method", "endpoint", "status_code", "service"],
)

bridge_http_request_duration_seconds = Histogram(
    "ddlnzb_bridge_http_request_duration_seconds",
    "Bridge HTTP request duration",
    ["method", "endpoint", "service"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)

bridge_http_requests_in_flight = Gauge(
    "ddlnzb_bridge_http_requests_in_flight",
    "In-flight bridge HTTP requests",
    ["service"],
)

bridge_queue_depth = Gauge(
    "ddlnzb_bridge_queue_depth",
    "Current bridge queue depth",
    ["state"],
)


def metrics_payload() -> tuple[bytes, str]:
    """Return metrics payload and content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
