# Contributing to Avtomatika Orchestrator

We welcome contributions! Please follow these guidelines:

## Setup

1.  Clone the repository and navigate to this directory.
2.  Install this package in editable mode with all dependencies:
    ```bash
    pip install -e .[all,test]
    ```

## Testing

Run the test suite specifically for the orchestrator:
```bash
pytest tests/
```

## Standards

-   Ensure all changes are linted with **Ruff**.
-   All new functions must have type hints and pass **Mypy** checks.
-   Follow the existing style of state-machine handlers.
