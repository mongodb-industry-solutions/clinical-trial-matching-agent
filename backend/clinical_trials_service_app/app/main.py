from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import ensure_indexes_and_seed_rule_families
from app.routes.clinical_trials import router as clinical_trials_router


settings = get_settings()
app = FastAPI(title="Leafy Clinical Trials API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_indexes_and_seed_rule_families()


app.include_router(clinical_trials_router)
