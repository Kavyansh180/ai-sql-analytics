"""AWS Lambda entrypoint adapter using Mangum for FastAPI."""
import logging
from mangum import Mangum
from src.api.main import app

logger = logging.getLogger(__name__)

# Mangum wraps the FastAPI application for AWS Lambda Function URL and API Gateway.
# Setting lifespan="off" prevents ASGI lifespan churn on serverless invocations.
handler = Mangum(app, lifespan="off")
