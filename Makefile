.PHONY: start start-fair stop restart restart-fair build build-executor rebuild logs shell clean clean-jobs reset-db help deploy status fair-status quality-fast quality-contract quality-integration

# Deployment configuration
DEPLOY_HOST ?= gassh
DEPLOY_DIR ?= ~/openscientist
COMPOSE_FILE ?= docker-compose.yml
FAIR_COMPOSE_FILES ?= -f docker-compose.yml -f docker-compose.fair-vcg.yml

# Load .env if present so OPENSCIENTIST_*_IMAGE values steer build tags
# (same source of truth as docker-compose substitution).
-include .env
export

# Image tags — derived from .env, fall back to :latest when unset.
# Set OPENSCIENTIST_AGENT_IMAGE=openscientist-agent:staging in
# ~/shandy-staging/.env to make `make build` produce :staging-tagged
# images automatically and never clobber prod's :latest tags.
BASE_IMAGE     ?= $(if $(OPENSCIENTIST_BASE_IMAGE),$(OPENSCIENTIST_BASE_IMAGE),openscientist-base:latest)
EXECUTOR_IMAGE ?= $(if $(OPENSCIENTIST_EXECUTOR_IMAGE),$(OPENSCIENTIST_EXECUTOR_IMAGE),openscientist-executor:latest)
AGENT_IMAGE    ?= $(if $(OPENSCIENTIST_AGENT_IMAGE),$(OPENSCIENTIST_AGENT_IMAGE),openscientist-agent:latest)

# Default target
help:
	@echo "OpenScientist - Makefile commands"
	@echo ""
	@echo "Docker:"
	@echo "  make build      - Build all Docker images (base, main, agent, executor)"
	@echo "  make build-executor - Build only the isolated code executor image"
	@echo "  make start      - Start OpenScientist with the required FAIR-VCG runtime"
	@echo "  make start-fair - Explicit alias for the FAIR-VCG runtime start"
	@echo "  make stop       - Stop containers"
	@echo "  make restart    - Restart the governed DVC runtime (no rebuild)"
	@echo "  make restart-fair - Reconcile the FAIR-VCG stack without dropping its overlay"
	@echo "  make rebuild    - Rebuild images and restart"
	@echo "  make logs       - Tail container logs"
	@echo "  make shell      - Open shell in main container"
	@echo "  make clean      - Remove containers and volumes"
	@echo "  make reset-db   - Flush database and run migrations"
	@echo ""
	@echo "Quality:"
	@echo "  make quality-fast        - Lock, compile, lint, format, and type checks"
	@echo "  make quality-contract    - Governed DVC and preclinical contract tests"
	@echo "  make quality-integration - Full coverage suite (requires Docker)"
	@echo ""
	@echo "Deployment:"
	@echo "  make deploy     - Deploy to production server"

start:
	@echo "Starting OpenScientist with required FAIR-VCG governance..."
	docker compose $(FAIR_COMPOSE_FILES) up -d --remove-orphans
	@echo "OpenScientist and FAIR-VCG started at http://localhost:8080"

start-fair:
	@echo "Starting OpenScientist with FAIR-VCG..."
	docker compose $(FAIR_COMPOSE_FILES) up -d --remove-orphans
	@echo "OpenScientist and FAIR-VCG started at http://localhost:8080"

stop:
	@echo "Stopping OpenScientist..."
	docker compose $(FAIR_COMPOSE_FILES) down --remove-orphans
	@echo "OpenScientist stopped"

restart: restart-fair

restart-fair:
	@echo "Reconciling OpenScientist with the FAIR-VCG runtime overlay..."
	docker compose $(FAIR_COMPOSE_FILES) up -d --remove-orphans
	@echo "OpenScientist and FAIR-VCG reconciled at http://localhost:8080"

build:
	@echo "Building base image (Python, uv) as $(BASE_IMAGE)..."
	DOCKER_DEFAULT_PLATFORM=linux/amd64 docker build -f Dockerfile.base -t $(BASE_IMAGE) .
	@echo "Building OpenScientist main image..."
	DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose $(FAIR_COMPOSE_FILES) build \
		--build-arg OPENSCIENTIST_COMMIT=$$(git rev-parse --short HEAD 2>/dev/null || echo "unknown") \
		--build-arg BUILD_TIME=$$(date -u +%Y-%m-%dT%H:%M:%SZ)
	$(MAKE) build-executor
	@echo "Building agent image as $(AGENT_IMAGE)..."
	DOCKER_DEFAULT_PLATFORM=linux/amd64 docker build \
		--secret id=github_token,env=GITHUB_TOKEN \
		-f Dockerfile.agent -t $(AGENT_IMAGE) .
	@echo "All images built: $(BASE_IMAGE), openscientist, $(EXECUTOR_IMAGE), $(AGENT_IMAGE)"

build-executor:
	@echo "Building executor image as $(EXECUTOR_IMAGE)..."
	docker build --platform linux/amd64 -f Dockerfile.executor -t $(EXECUTOR_IMAGE) .

rebuild: build
	docker compose $(FAIR_COMPOSE_FILES) down --remove-orphans
	docker compose $(FAIR_COMPOSE_FILES) up -d --remove-orphans
	@echo "OpenScientist rebuilt and started at http://localhost:8080"

logs:
	@echo "Tailing OpenScientist logs (Ctrl+C to exit)..."
	docker compose $(FAIR_COMPOSE_FILES) logs -f

shell:
	@echo "Opening shell in OpenScientist container..."
	docker compose $(FAIR_COMPOSE_FILES) exec openscientist /bin/bash

clean:
	@echo "Removing containers and volumes..."
	docker compose $(FAIR_COMPOSE_FILES) down -v --remove-orphans
	@echo "Cleaned up"

clean-jobs:
	@echo "Cleaning up old job directories..."
	@read -p "Delete jobs older than how many days? [7]: " days; \
	days=$${days:-7}; \
	docker compose $(FAIR_COMPOSE_FILES) exec openscientist python -m openscientist.job_manager cleanup --days $$days
	@echo "Job cleanup complete"

reset-db:
	@echo "WARNING: This will delete all database data!"
	@read -p "Are you sure? [y/N]: " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		set -e; \
		echo "Stopping containers and removing volumes..."; \
		docker compose $(FAIR_COMPOSE_FILES) down -v --remove-orphans; \
		echo "Starting postgres..."; \
		docker compose $(FAIR_COMPOSE_FILES) up -d postgres; \
		echo "Waiting for postgres to be ready..."; \
		until docker compose $(FAIR_COMPOSE_FILES) exec -T postgres pg_isready -U $${POSTGRES_USER:-openscientist} -d $${POSTGRES_DB:-openscientist} >/dev/null 2>&1; do \
			sleep 1; \
		done; \
		echo "Running migrations..."; \
		docker compose $(FAIR_COMPOSE_FILES) run --rm --no-deps openscientist alembic upgrade head; \
		echo "Starting application..."; \
		docker compose $(FAIR_COMPOSE_FILES) up -d --remove-orphans openscientist; \
		echo "Database reset complete!"; \
	else \
		echo "Aborted."; \
	fi

# Show job status
status:
	@echo "Job status:"
	docker compose $(FAIR_COMPOSE_FILES) exec openscientist python -m openscientist.job_manager summary

fair-status:
	docker compose $(FAIR_COMPOSE_FILES) ps

quality-fast:
	uv run python -m openscientist.quality fast

quality-contract:
	uv run python -m openscientist.quality contract

quality-integration:
	uv run python -m openscientist.quality integration

# Deploy to production server
deploy:
	@echo "========================================="
	@echo "Deploying OpenScientist to $(DEPLOY_HOST)"
	@echo "========================================="
	@echo ""
	@echo "Step 1: Ensuring repository exists on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "if [ ! -d $(DEPLOY_DIR)/.git ]; then \
		echo 'ERROR: Repository not found at $(DEPLOY_DIR)'; \
		echo 'Please clone the repository first:'; \
		echo '  ssh $(DEPLOY_HOST)'; \
		echo '  git clone <your-repo-url> $(DEPLOY_DIR)'; \
		exit 1; \
	else \
		echo 'Repository exists, pulling latest changes...'; \
		cd $(DEPLOY_DIR) && git pull; \
	fi"
	@echo ""
	@echo "Step 2: Checking .env configuration on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "if [ ! -f $(DEPLOY_DIR)/.env ]; then \
		echo 'WARNING: .env does not exist on server!'; \
		echo 'You need to create it manually:'; \
		echo '  1. ssh $(DEPLOY_HOST)'; \
		echo '  2. cd $(DEPLOY_DIR) && cp .env.example .env'; \
		echo '  3. Edit .env with production values (ANTHROPIC_AUTH_TOKEN, etc.)'; \
		echo 'Deployment will continue, but app will not start without .env'; \
	else \
		echo '.env already exists - preserving existing configuration'; \
	fi"
	@echo ""
	@echo "Step 3: Building and restarting application on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && make rebuild"
	@echo ""
	@echo "Step 4: Running database migrations on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && docker compose $(FAIR_COMPOSE_FILES) exec openscientist alembic upgrade head"
	@echo ""
	@echo "========================================="
	@echo "Deployment complete!"
	@echo "Application should be running at https://chat.alzassistant.org"
	@echo "========================================="
