"""Tests for the /query endpoint."""


class TestQueryEndpoint:
    def test_query_returns_response(self, client, config):
        """Verify that asking a question returns a valid response."""
        question = config["query"]["default_question"]
        response = client.query(question)

        assert response.status_code == 200

        data = response.json()
        assert "response" in data
        assert len(data["response"]) > 0
