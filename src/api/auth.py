"""Authentication and request authorization for AI SQL Analytics Copilot API."""
import logging
import secrets
from typing import Optional
from fastapi import Header, HTTPException, status

from src.core.config import get_settings

logger = logging.getLogger(__name__)

COPILOT_SECRET_HEADER = "X-Copilot-Secret"


def verify_copilot_secret(
    x_copilot_secret: Optional[str] = Header(None, alias=COPILOT_SECRET_HEADER),
) -> None:
    """
    Verify the shared secret header X-Copilot-Secret.
    
    Security rules:
    1. In production (ENVIRONMENT in ('production', 'prod')):
       - Fails closed: raises 401 if COPILOT_API_SECRET is unset or whitespace on the server.
       - Raises 401 if X-Copilot-Secret is missing from the request.
       - Compares provided secret against server secret using constant-time comparison
         (secrets.compare_digest) to prevent timing attacks.
    2. In development and test environments:
       - If COPILOT_API_SECRET is configured, enforces matching header.
       - If COPILOT_API_SECRET is unset, permits request to maintain seamless local development.
    3. Rejection happens before invoking LLM or executing database queries.
    4. Never logs secret values or includes secret values in error responses.
    """
    settings = get_settings()
    is_prod = settings.is_production()
    server_secret = settings.COPILOT_API_SECRET

    if is_prod:
        if not server_secret or not server_secret.strip():
            logger.error(
                "Authentication misconfiguration: COPILOT_API_SECRET is unset in production."
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Server authentication is misconfigured or missing.",
            )
        if not x_copilot_secret or not x_copilot_secret.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Missing X-Copilot-Secret authentication header.",
            )
        if not secrets.compare_digest(x_copilot_secret.strip(), server_secret.strip()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Invalid X-Copilot-Secret authentication header.",
            )
    else:
        # Development / test environment
        if server_secret and server_secret.strip():
            if not x_copilot_secret or not x_copilot_secret.strip():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized: Missing X-Copilot-Secret authentication header.",
                )
            if not secrets.compare_digest(x_copilot_secret.strip(), server_secret.strip()):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Unauthorized: Invalid X-Copilot-Secret authentication header.",
                )
