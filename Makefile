PY := uv run --locked
WEB := npm --prefix apps/web

.PHONY: lint typecheck test integration-test e2e screenshots seed eval fixtures migrate contracts
lint:
	$(PY) ruff check .
	$(PY) ruff format --check .
	$(WEB) run lint

typecheck:
	$(PY) mypy app migrations scripts tests
	$(WEB) run typecheck

test:
	$(PY) pytest -m 'not integration'

integration-test:
	$(PY) pytest -m integration

e2e:
	$(WEB) test -- --grep 'workspace workflow'

screenshots:
	$(WEB) test -- --grep 'portfolio capture'
	$(PY) python -m scripts.cover

seed:
	$(PY) python -m scripts.demo seed

eval:
	$(PY) python -m scripts.demo eval

fixtures:
	$(PY) python -m scripts.generate_samples

migrate:
	docker compose run --rm migrate

contracts:
	mkdir -p packages/contracts
	$(PY) python -c 'import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))' > packages/contracts/openapi.json
	$(WEB) run contracts
