# Docker Compose 操作入口。make 或 make help 顯示所有指令。
# 可覆寫：make up FRONTEND_PORT=3000 BACKEND_PORT=8001 PROJECT_NAME=nightwatch-shop-web
# 舊版獨立 Compose 可用：make up COMPOSE=docker-compose
COMPOSE ?= docker compose
PROJECT_NAME ?= nightwatch-shop-web
FRONTEND_PORT ?= 8080
BACKEND_PORT ?= 8000
export FRONTEND_PORT BACKEND_PORT
.DEFAULT_GOAL := help
.PHONY: help build rebuild up down stop restart logs ps config test smoke clean

help: ## 顯示各指令用途
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  make %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## 建置前後端 Docker images
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml build

rebuild: ## 不使用快取重新建置 images
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml build --no-cache

up: ## 建置並於背景啟動兩個 containers
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml up -d --build

down: ## 移除 containers 與網路，保留資料 volumes
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml down

stop: ## 停止 containers，保留 containers 與資料 volumes
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml stop

restart: ## 重新啟動 containers（程式變更請用 make up）
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml restart

logs: ## 持續檢視前後端 logs（Ctrl-C 離開）
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml logs -f --tail=100

ps: ## 查看 containers 狀態
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml ps

config: ## 驗證並顯示 Compose 設定
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml config

test: ## 啟動後執行 API 整合測試（會建立測試訂單）
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml exec -T backend python -m unittest discover -s tests -v

smoke: ## 驗證前端首頁與經過 Nginx 的 API
	curl --fail --silent --show-error http://localhost:$(FRONTEND_PORT)/ -o /dev/null
	curl --fail --silent --show-error http://localhost:$(FRONTEND_PORT)/api/health

clean: ## 刪除 containers、網路與所有資料 volumes，需 CONFIRM=yes
	@test "$(CONFIRM)" = "yes" || (echo '將永久刪除訂單與 logs。確認請執行 make clean CONFIRM=yes'; exit 1)
	$(COMPOSE) -p $(PROJECT_NAME) -f compose.yaml down --volumes --remove-orphans
