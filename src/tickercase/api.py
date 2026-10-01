"""HTTP API.

POST /claims/validate   -> validation issues, missing fields, input fingerprint
POST /claims/confirm    -> confirmation for exactly these inputs (422 if invalid)
POST /cases             -> run a confirmed case; returns CaseResult
GET  /cases/{case_id}   -> stored CaseResult
POST /claims/extract    -> ticker, target and horizon read from the claim sentence (to review before confirming)
GET  /reference/{ticker} -> public reference values (price, shares, latest annual metrics) to review before confirming
GET  /health
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .config import load_settings
from .models import CaseResult, CaseStatus, ClaimDraft, ClaimExtraction, Confirmation, ReferenceSnapshot, ValidationResult
from .service import CaseService
from .storage import CaseStore
from .validation import ConfirmationError, confirm, validate_draft

SecMode = Literal["live", "record", "replay", "synthetic"]


class ExtractRequest(BaseModel):
    text: str
    mode: Optional[SecMode] = None


class CaseRequest(BaseModel):
    draft: ClaimDraft
    confirmation: Optional[Confirmation] = None
    sec_mode: Optional[SecMode] = None


STATUS_CODES = {
    CaseStatus.EVALUATED: 200,
    CaseStatus.EVALUATED_WITH_PROVIDER_ERRORS: 200,
    CaseStatus.BLOCKED_INVALID_INPUT: 422,
    CaseStatus.BLOCKED_UNCONFIRMED: 409,
    CaseStatus.BLOCKED_CONFIRMATION_STALE: 409,
}


def create_app(service: Optional[CaseService] = None) -> FastAPI:
    if service is None:
        settings = load_settings()
        service = CaseService(settings, store=CaseStore(settings.case_dir))
    app = FastAPI(title="TickerCase", version=__version__)
    app.state.service = service

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "default_sec_mode": service.settings.sec_mode}

    @app.post("/claims/validate", response_model=ValidationResult)
    def validate(draft: ClaimDraft) -> ValidationResult:
        return validate_draft(draft, today=service.today())

    @app.post("/claims/confirm", response_model=Confirmation)
    def confirm_claim(draft: ClaimDraft):
        try:
            return confirm(draft, now=service.now, today=service.today())
        except ConfirmationError as exc:
            return JSONResponse(status_code=422, content=exc.result.model_dump(mode="json"))

    @app.post("/cases", response_model=CaseResult)
    def run_case(request: CaseRequest):
        mode = request.sec_mode or service.settings.sec_mode
        result = service.evaluate(request.draft, request.confirmation, sec_mode=mode)
        return JSONResponse(status_code=STATUS_CODES[result.status], content=result.model_dump(mode="json"))

    @app.post("/claims/extract", response_model=ClaimExtraction)
    def extract(request: ExtractRequest) -> ClaimExtraction:
        return service.extract(request.text, mode=request.mode or service.settings.sec_mode)

    @app.get("/reference/{ticker}", response_model=ReferenceSnapshot)
    def reference(ticker: str, mode: Optional[SecMode] = None):
        try:
            return service.reference_snapshot(ticker, mode=mode or service.settings.sec_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/cases/{case_id}", response_model=CaseResult)
    def get_case(case_id: str):
        if service.store is None:
            raise HTTPException(status_code=404, detail="case storage is not configured")
        try:
            result = service.store.load(case_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid case id")
        if result is None:
            raise HTTPException(status_code=404, detail="case not found")
        return result

    return app


def app_factory() -> FastAPI:
    """Entry point for `uvicorn --factory tickercase.api:app_factory`."""
    return create_app()
