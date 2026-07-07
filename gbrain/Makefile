# gbrain deploy — convenience targets.
#   make up            # build + start everything
#   make register-client NAME=alice SCOPES="read,write" SOURCE=default FED=default
.DEFAULT_GOAL := help
COMPOSE := docker compose

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

up: ## Build (if needed) and start all containers
	$(COMPOSE) up -d --build

down: ## Stop and remove containers (KEEPS volumes/data)
	$(COMPOSE) down

nuke: ## Stop and remove containers AND volumes (DESTROYS local state + tailscale identity)
	$(COMPOSE) down -v

restart: ## Restart all services
	$(COMPOSE) restart

logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=120

ps: ## Show container status
	$(COMPOSE) ps

doctor: ## Run gbrain health check against the DB (pgvector, RLS, embeddings)
	$(COMPOSE) exec gbrain-serve gbrain doctor

health: ## Curl the internal /health endpoint
	@$(COMPOSE) exec gbrain-serve curl -fsS http://localhost:3131/health; echo

stats: ## Show brain stats (page/chunk counts)
	$(COMPOSE) exec gbrain-serve gbrain stats

ts-status: ## Tailscale node status (shows the public DNS name once connected)
	$(COMPOSE) exec tailscale tailscale status

ts-funnel: ## Show the active Funnel config / public URL
	$(COMPOSE) exec tailscale tailscale funnel status

auth-list: ## List registered OAuth clients / tokens
	$(COMPOSE) exec gbrain-serve gbrain auth list

# Register a machine-to-machine client (a teammate or an agent).
#   make register-client NAME=alice SCOPES="read,write" SOURCE=default FED=default
register-client: ## Register an OAuth client_credentials client (NAME=.. [SCOPES=..] [SOURCE=..] [FED=..])
	$(COMPOSE) exec gbrain-serve gbrain auth register-client $(NAME) \
		--grant-types client_credentials \
		--scopes "$(or $(SCOPES),read,write)" \
		$(if $(SOURCE),--source $(SOURCE),) \
		$(if $(FED),--federated-read $(FED),)

# Register the ChatGPT connector client (authorization_code + PKCE; no secret).
# Get REDIRECT from the ChatGPT connector setup screen, e.g.
#   https://chatgpt.com/connector_platform_oauth_redirect
register-chatgpt: ## Register the ChatGPT client (REDIRECT=<chatgpt redirect uri>)
	$(COMPOSE) exec gbrain-serve gbrain auth register-client chatgpt \
		--grant-types authorization_code \
		--scopes "read,write" \
		--redirect-uri "$(REDIRECT)"

.PHONY: help up down nuke restart logs ps doctor health stats ts-status ts-funnel auth-list register-client register-chatgpt
