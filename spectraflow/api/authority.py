"""Authority-drift observability API.

The endpoint evaluates evidence only. It does not authorize or dispatch calls.
"""

from fastapi import APIRouter

from spectraflow.authority.drift import (
    AuthorityDriftRequest,
    AuthorityDriftResult,
    evaluate_authority_drift,
)

router = APIRouter()


@router.post("/authority/evaluate", response_model=AuthorityDriftResult)
async def evaluate_authority(body: AuthorityDriftRequest) -> AuthorityDriftResult:
    return evaluate_authority_drift(body)
