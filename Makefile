# Aperture - common operations. `make help` lists everything.
COMPOSE ?= docker compose
GPU_FILES = -f docker-compose.yml -f docker-compose.gpu.yml

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this list
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-22s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Build and start the stack (http://localhost:8080)
	@test -f .env || cp .env.example .env
	$(COMPOSE) up -d --build
	@echo "\nAperture is starting. UI: http://localhost:8080  ·  API docs: http://localhost:8080/api/docs"

.PHONY: up-gpu
up-gpu: ## Start with the CUDA runtime (needs the NVIDIA container toolkit)
	@test -f .env || cp .env.example .env
	$(COMPOSE) $(GPU_FILES) up -d --build

.PHONY: up-full
up-full: ## Start with Prometheus, Grafana, the collector and Jaeger
	@test -f .env || cp .env.example .env
	AP_OTEL_ENABLED=true $(COMPOSE) --profile observability --profile tracing up -d --build

.PHONY: down
down: ## Stop everything (volumes survive)
	$(COMPOSE) down

.PHONY: reset
reset: ## Stop everything and delete the data volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Follow logs for the application services
	$(COMPOSE) logs -f api worker logsink runtime

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: scale-runtime
scale-runtime: ## Scale runtime containers: make scale-runtime N=4
	$(COMPOSE) up -d --scale runtime=$(or $(N),2) runtime

.PHONY: migrate
migrate: ## Apply database migrations
	$(COMPOSE) run --rm api alembic upgrade head

.PHONY: revision
revision: ## Autogenerate a migration: make revision M="add thing"
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(M)"

.PHONY: shell
shell: ## Open a shell in the API container
	$(COMPOSE) exec api sh

.PHONY: psql
psql: ## Open psql against the database
	$(COMPOSE) exec postgres psql -U aperture -d aperture

.PHONY: test
test: ## Run the pipeline, artifact and runtime test suites
	cd core && uv run --with numpy --with pillow --with pydantic --with-editable . python smoke_test.py
	cd core && uv run --with numpy --with pillow --with pydantic --with onnx --with-editable . python artifact_test.py
	cd runtime && uv run --with onnx python smoke_test.py

.PHONY: e2e
e2e: ## Exercise the running stack end to end (upload, validate, deploy, predict, roll back)
	./scripts/e2e.sh

.PHONY: verify-real
verify-real: ## Verify against the real cells-detection classifier (see scripts/real-model)
	@echo "1) export the checkpoint from the cells-detection repo:"
	@echo "   cd ../cells-detection && uv run --no-sync --with onnx python \\"
	@echo "     ../aperture/scripts/real-model/export.py /tmp/aperture-real"
	@echo "2) then run the three checks:"
	uv run --no-project --with httpx python scripts/real-model/verify.py
	uv run --no-project --with httpx python scripts/real-model/workflow_test.py
	uv run --no-project --with httpx python scripts/real-model/roboflow_format.py

.PHONY: seed
seed: ## Create a demo model with a synthetic ONNX detector and deploy it
	./scripts/seed-demo.sh

.PHONY: lint
lint: ## Ruff over the Python, tsc over the frontend
	cd backend && uv run ruff check app
	cd runtime && uv run ruff check runtime || true
	cd frontend && npx tsc -b --noEmit

.PHONY: openapi
openapi: ## Write the OpenAPI document to openapi.json
	$(COMPOSE) exec api python -c "import json,app.main as m; print(json.dumps(m.app.openapi()))" > openapi.json
	@echo "wrote openapi.json"

.PHONY: sdk
sdk: openapi ## Generate the Python and TypeScript clients from the spec
	./scripts/generate-sdk.sh
