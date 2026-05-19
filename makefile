build:
	docker-compose up --build -d

start: 
	docker-compose start

stop:
	docker-compose stop

clean:
	docker-compose down --rmi all -v

install_uv:
	curl -LsSf https://astral.sh/uv/install.sh | sh

uv_init:
	cd backend && uv venv

uv_sync:
	cd backend && uv sync

uv_update:
	cd backend && uv lock --upgrade

backend-install:
	cd backend && uv sync

backend-dev:
	cd backend && uv run uvicorn main:app --reload --port 8014

backend-test:
	cd backend && PYTHONPATH=clinical_trials_service_app uv run python -m unittest discover -s clinical_trials_service_app/tests -v
	cd backend && PYTHONPATH=src/clinical_trial_matching_agent/leafy_extract:src/clinical_trial_matching_agent/leafy_extract/services uv run python -m unittest discover -s src/clinical_trial_matching_agent/leafy_extract/tests -v

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

demo-start:
	./scripts/start-demo.sh

demo-stop:
	./scripts/stop-demo.sh

verify: backend-test frontend-build

dev-help:
	@echo "1. cp backend/.env.example backend/.env"
	@echo "2. cp frontend/.env.local.example frontend/.env.local"
	@echo "3. make backend-install"
	@echo "4. make frontend-install"
	@echo "5. Run 'make backend-dev' and 'make frontend-dev' in separate terminals"
	@echo "6. Run 'make verify' before shipping changes"
