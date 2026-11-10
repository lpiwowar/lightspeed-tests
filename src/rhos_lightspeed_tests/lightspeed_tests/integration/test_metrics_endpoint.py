"""Tests for the /metrics endpoint."""

EXPECTED_METRICS = [
    "TYPE ls_rest_api_calls_total counter",
    "TYPE ls_response_duration_seconds histogram",
    "TYPE ls_provider_model_configuration gauge",
    "TYPE ls_llm_calls_total counter",
    "TYPE ls_llm_calls_failures_total counter",
    "TYPE ls_llm_validation_errors_total counter",
    "TYPE ls_llm_token_sent_total counter",
    "TYPE ls_llm_token_received_total counter",
    "TYPE ls_started_in_degraded_mode gauge",
]


class TestMetricsEndpoint:
    def test_metrics_return_response(self, client):
        """Verify that /metrics endpoints returns a correct response containing expected metrics."""
        response = client.metrics()

        assert response.status_code == 200
        for expected_metric in EXPECTED_METRICS:
            assert expected_metric in response.text
