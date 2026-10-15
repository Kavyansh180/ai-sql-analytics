"""API router endpoints for AI-Powered SQL Analytics Copilot."""
import logging
from fastapi import APIRouter, Depends, HTTPException, status

from src.api.auth import verify_copilot_secret
from src.api.rate_limiter import check_rate_limit
from src.api.schemas import AnalyticsQueryRequest, AnalyticsQueryResponse
from src.llm.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseValidationError,
    LLMTimeoutError,
)
from src.llm.service import NaturalLanguageSQLService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


def get_analytics_service() -> NaturalLanguageSQLService:
    """Dependency provider for NaturalLanguageSQLService."""
    return NaturalLanguageSQLService()


@router.post(
    "/query",
    response_model=AnalyticsQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Translate Natural Language to SQL & Execute",
    description="Accepts a natural language business question, generates SQL via LLM, enforces security guardrails, executes on PostgreSQL, and returns data.",
    dependencies=[Depends(verify_copilot_secret), Depends(check_rate_limit)],
)
def query_analytics(
    request: AnalyticsQueryRequest,
    service: NaturalLanguageSQLService = Depends(get_analytics_service),
) -> AnalyticsQueryResponse:
    """Execute end-to-end natural-language-to-SQL flow."""
    try:
        result = service.execute_question(request.question)
        return AnalyticsQueryResponse(
            status=result.status,
            question=result.question,
            sql=result.sql,
            explanation=result.explanation,
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            execution_time_ms=result.execution_time_ms,
            clarification_question=result.clarification_question,
            error=result.error,
        )
    except LLMConfigurationError as e:
        logger.error("LLM configuration error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service configuration is missing or incomplete on this server.",
        )
    except LLMTimeoutError as e:
        logger.error("LLM timeout: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The AI query generation service timed out. Please try again.",
        )
    except (LLMProviderError, LLMResponseValidationError) as e:
        logger.error("LLM provider error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate SQL from AI provider. Please try again.",
        )
    except Exception as e:
        logger.exception("Unexpected error in query_analytics endpoint: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal server error occurred while processing your query.",
        )
