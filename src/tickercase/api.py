"""HTTP API.

POST /claims/validate   -> validation issues, missing fields, input fingerprint
POST /claims/confirm    -> confirmation for exactly these inputs (422 if invalid)
POST /cases             -> run a confirmed case; returns CaseResult
GET  /cases/{case_id}   -> stored CaseResult
POST /claims/extract    -> ticker, target and horizon read from the claim sentence (to review before confirming)
GET  /reference/{ticker} -> public reference values (price, shares, latest annual metrics) to review before confirming
GET  /health

A2 Acquire Dated Evidence on its own (contracts/api-v1.yaml):
POST /evidence          -> Evidence Query, Evidence Set and Source Error Records of a confirmed claim (A2)
POST /evidence/filings  -> Filing Records  (IO.37, A2.1 -> A2.4)
POST /evidence/market   -> Market Records  (IO.38, A2.2 -> A2.4)
POST /evidence/context  -> Context Records (IO.39, A2.3 -> A2.4)
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .acquisition import ContextRecords, EvidenceSet, FilingRecords, MarketRecords
from .config import load_settings
from .models import ProviderErrorRecord, CaseResult, CaseStatus, ClaimDraft, ClaimExtraction, Confirmation, ReferenceSnapshot, ValidationResult
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


class EvidenceResponse(BaseModel):
    """The outputs of A2: O IO.6 Evidence Query, O IO.29 Evidence Set and O IO.30 Source Error Records."""

    function_id: str = "A2"
    status: CaseStatus
    evidence_query: list[str] = []
    evidence_set: Optional[EvidenceSet] = None
    source_error_records: list[ProviderErrorRecord] = []


class FlowResponse(BaseModel):
    """One internal flow of the A2 diagram, with the requests it sent and the failures it isolated."""

    function_id: str
    flow_id: str
    status: CaseStatus
    evidence_query: list[str] = []
    filing_records: Optional[FilingRecords] = None
    market_records: Optional[MarketRecords] = None
    context_records: Optional[ContextRecords] = None
    source_error_records: list[ProviderErrorRecord] = []


FLOWS = {  # path -> (second-level function, internal flow, field)
    "filings": ("A2.1", "IO.37", "filing_records"),
    "market": ("A2.2", "IO.38", "market_records"),
    "context": ("A2.3", "IO.39", "context_records"),
}

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

    @app.get("/health", operation_id="getHealth")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "default_sec_mode": service.settings.sec_mode}

    @app.post("/claims/validate", response_model=ValidationResult, operation_id="validateClaim")
    def validate(draft: ClaimDraft) -> ValidationResult:
        return validate_draft(draft, today=service.today())

    @app.post("/claims/confirm", response_model=Confirmation, operation_id="confirmClaim")
    def confirm_claim(draft: ClaimDraft):
        try:
            return confirm(draft, now=service.now, today=service.today())
        except ConfirmationError as exc:
            return JSONResponse(status_code=422, content=exc.result.model_dump(mode="json"))

    @app.post("/cases", response_model=CaseResult, operation_id="runCase")
    def run_case(request: CaseRequest):
        mode = request.sec_mode or service.settings.sec_mode
        result = service.evaluate(request.draft, request.confirmation, sec_mode=mode)
        return JSONResponse(status_code=STATUS_CODES[result.status], content=result.model_dump(mode="json"))

    @app.post("/evidence", response_model=EvidenceResponse, operation_id="acquireDatedEvidence")
    def acquire_evidence(request: CaseRequest):
        mode = request.sec_mode or service.settings.sec_mode
        status, acquired = service.acquire(request.draft, request.confirmation, sec_mode=mode)
        body = EvidenceResponse(status=status)
        if acquired is not None:
            body = EvidenceResponse(status=status, evidence_query=acquired.evidence_query, evidence_set=acquired.evidence_set,
                                    source_error_records=acquired.source_error_records)
        return JSONResponse(status_code=STATUS_CODES[status], content=body.model_dump(mode="json"))

    def flow_operation(name: str):
        function_id, flow_id, field_name = FLOWS[name]

        def operation(request: CaseRequest):
            mode = request.sec_mode or service.settings.sec_mode
            status, acquired = service.acquire(request.draft, request.confirmation, sec_mode=mode, flow=name)
            values = dict(function_id=function_id, flow_id=flow_id, status=status)
            if acquired is not None:
                records = {"filings": acquired.evidence_set.filing, "market": acquired.evidence_set.market,
                           "context": acquired.evidence_set.context}[name]
                values.update({field_name: records, "evidence_query": acquired.evidence_query,
                               "source_error_records": acquired.source_error_records})
            return JSONResponse(status_code=STATUS_CODES[status], content=FlowResponse(**values).model_dump(mode="json", exclude_none=True))

        return operation

    app.post("/evidence/filings", response_model=FlowResponse, operation_id="requestFilingsAndReportedFacts")(flow_operation("filings"))
    app.post("/evidence/market", response_model=FlowResponse, operation_id="requestMarketAndOptionData")(flow_operation("market"))
    app.post("/evidence/context", response_model=FlowResponse, operation_id="requestContextSignals")(flow_operation("context"))

    @app.post("/claims/extract", response_model=ClaimExtraction, operation_id="extractClaim")
    def extract(request: ExtractRequest) -> ClaimExtraction:
        return service.extract(request.text, mode=request.mode or service.settings.sec_mode)

    @app.get("/reference/{ticker}", response_model=ReferenceSnapshot, operation_id="getReference")
    def reference(ticker: str, mode: Optional[SecMode] = None):
        try:
            return service.reference_snapshot(ticker, mode=mode or service.settings.sec_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/cases/{case_id}", response_model=CaseResult, operation_id="getCase")
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
