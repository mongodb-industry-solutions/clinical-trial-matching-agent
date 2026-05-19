from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RemoteSearchRequest(BaseModel):
    condition: str = Field(default="breast cancer", min_length=1)
    phase: str | None = None
    sponsor: str | None = None
    nct_id: str | None = None
    page_size: int = Field(default=20, ge=1, le=100)
    max_pages: int = Field(default=3, ge=1, le=20)
    statuses: list[str] | None = None


class IngestTrialRequest(BaseModel):
    nct_id: str = Field(min_length=4)
    trigger: str = "manual_demo"
    condition: str | None = None
    phase: str | None = None
    sponsor: str | None = None


class BulkIngestRequest(BaseModel):
    condition: str = Field(default="breast cancer", min_length=1)
    phase: str | None = "PHASE3"
    sponsor: str | None = None
    statuses: list[str] | None = None
    page_size: int = Field(default=25, ge=1, le=100)
    max_pages: int = Field(default=5, ge=1, le=20)
    limit: int | None = Field(default=50, ge=1, le=500)
    trigger: str = "bulk_gui_demo"


class PlaygroundCompileRequest(BaseModel):
    text: str = Field(min_length=1)
    section: Literal["inclusion", "exclusion"] = "inclusion"
    ancestor_headers: list[str] | None = None


class RecompileTrialRequest(BaseModel):
    trigger: str = "review_recompile"
    reason: str | None = None


class RecompileFamilyRequest(BaseModel):
    trigger: str = "family_recompile"
    reason: str | None = None


class PatientToTrialsMatchRequest(BaseModel):
    trial_ids: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=200)
    population_mode: Literal["authoritative_oncology", "pharmacy_prescreen"] = "authoritative_oncology"
    stage_target_window: Literal["stage_ii_iii_or_locally_advanced", "advanced_or_metastatic", "mixed_or_manual", "not_stage_specific"] | None = None
    run_type: str | None = None
    benchmark_pack_id: str | None = None
    benchmark_label: str | None = None


class TrialToPatientsMatchRequest(BaseModel):
    patient_refs: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=500)
    include_conflict_patients: bool = False
    population_mode: Literal["authoritative_oncology", "pharmacy_prescreen"] = "authoritative_oncology"
    run_type: str | None = None
    benchmark_pack_id: str | None = None
    benchmark_label: str | None = None


class ReviewApplyRequest(BaseModel):
    criterion_id: str = Field(min_length=8)
    decision: Literal["confirm_family", "manual_review_clinical", "not_patient_rule"]
    final_family_id: str | None = None
    slot_overrides: dict | None = None
    final_compiled_rule: dict | None = None
    reviewer: str | None = None
    notes: str | None = None
    recompile_target: Literal["none", "trial", "family"] = "trial"
    recompile_reason: str | None = None


class TerminologyResolveRequest(BaseModel):
    text: str = Field(min_length=1)
    vocabulary: Literal["snomed"] = "snomed"
    top_k: int = Field(default=10, ge=1, le=50)
    context: dict | None = None


class BindingValidateRequest(BaseModel):
    code: str = Field(min_length=1)
    system: str | None = None
    display: str | None = None
    context: dict | None = None
