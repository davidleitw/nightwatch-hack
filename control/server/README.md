# NightWatch control server

Run the existing FastAPI app with `uv run --locked uvicorn main:app --host
127.0.0.1 --port 8001` from this directory. The investigation manager stores
sessions in `../.data/investigations.sqlite3` by default; set
`NIGHTWATCH_INVESTIGATION_DB` to use another file and
`NIGHTWATCH_GRAPH_URL` to select the operator configured graph source.

Production investigations use `NIGHTWATCH_LLM_API_KEY` (or
`OPENAI_API_KEY`) and `NIGHTWATCH_LLM_MODEL`. Offline tests may inject a Python
model factory through `install_investigations(..., model_factory=...)` or by
replacing `app.state.investigation_manager.model_factory` before application
startup. HTTP clients cannot select either the model or graph URL.
