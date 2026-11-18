# rhos-lightspeed-tests
# Hello world
# Tortugaa
# Tortuga 3
# Tortuga 4
Test suite for RHOS Lightspeed in RHOSO environments.

## Setup

```bash
uv venv
uv sync
```

## Configuration

Default settings are in `config/default.yaml`:

```yaml
rhos_lightspeed:
  base_url: "http://localhost:8080"
  timeout: 30

query:
  default_question: "What is OpenStack?"
```

Override the endpoint via environment variable:

```bash
export RHOS_LIGHTSPEED_URL=http://rhos-lightspeed:8080
```

## Running tests

```bash
# Run all tests
uv run pytest

# Run integration tests only
uv run pytest tests/integration/

# Generate JUnit XML (automatic via pyproject.toml config)
uv run pytest  # outputs test-results.xml
```

## Adding tests

1. Create test file in appropriate directory:
   - `tests/unit/` - Fast, isolated tests
   - `tests/integration/` - Tests requiring deployed service
   - `tests/e2e/` - Full end-to-end scenarios

2. Use the shared fixtures from `conftest.py`:
   ```python
   class TestMyFeature:
       def test_something(self, client, config):
           response = client.query("my question")
           assert response.status_code == 200
   ```

3. Run: `uv run pytest tests/integration/test_my_feature.py`

## Project structure

```
rhos-lightspeed-tests/
├── config/
│   └── default.yaml                 # Default test configuration
├── src/rhos_lightspeed_tests/       # Shared utilities
│   ├── __init__.py
│   ├── client.py                    # API client (query, health)
│   └── config.py                    # Reads default.yaml, env var overrides
├── tests/
│   ├── conftest.py                  # Shared fixtures (config, client)
│   ├── unit/
│   ├── integration/
│   │   └── test_query_endpoint.py   # First test: /query endpoint
│   └── e2e/
├── pyproject.toml
└── README.md
```
