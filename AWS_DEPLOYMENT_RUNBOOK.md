# AWS Production Deployment Runbook: AI-Powered SQL Analytics Copilot

**Target Environment:** AWS `ap-south-1` (Mumbai) & Streamlit Community Cloud  
**Document Status:** Pre-Deployment Execution Guide (Preparation Only — No Cloud Resources Created)  
**Security Baseline:** Phase 1 Hardened (AST SQL Validation, Read-Only PostgreSQL, Constant-Time Secret Verification, Fail-Closed Config)

---

## Table of Contents
1. [Target Architecture & Data Flow](#1-target-architecture--data-flow)
2. [Security, Governance & Abuse-Resistance Architecture](#2-security-governance--abuse-resistance-architecture)
3. [IAM, KMS & Permissions Matrix](#3-iam-kms--permissions-matrix)
4. [Cost Estimation & Operational Planning](#4-cost-estimation--operational-planning)
5. [Step-by-Step Deployment Stages](#5-step-by-step-deployment-stages)
   - [Stage 1: Local Pre-Deployment Verification & Clean Git Setup](#stage-1-local-pre-deployment-verification--clean-git-setup)
   - [Stage 2: AWS Account, CLI & Identity Verification](#stage-2-aws-account-cli--identity-verification)
   - [Stage 3: IAM Lambda Execution Role Creation](#stage-3-iam-lambda-execution-role-creation)
   - [Stage 4: Secure Production Secret Storage in SSM Parameter Store](#stage-4-secure-production-secret-storage-in-ssm-parameter-store)
   - [Stage 5: Amazon ECR Private Repository & Image Publishing](#stage-5-amazon-ecr-private-repository--image-publishing)
   - [Stage 6: AWS Lambda Function Creation & Serverless Configuration](#stage-6-aws-lambda-function-creation--serverless-configuration)
   - [Stage 7: Lambda Function URL & Shared-Secret Auth Verification](#stage-7-lambda-function-url--shared-secret-auth-verification)
   - [Stage 8: Lambda Networking, Neon TLS Connectivity & Pool Lifecycle](#stage-8-lambda-networking-neon-tls-connectivity--pool-lifecycle)
   - [Stage 9: Streamlit Community Cloud Deployment & Integration](#stage-9-streamlit-community-cloud-deployment--integration)
   - [Stage 10: End-to-End Functional, Security & Guardrail Verification](#stage-10-end-to-end-functional-security--guardrail-verification)
   - [Stage 11: Monitoring, Cost Alerting, Rollback & Emergency Shutdown](#stage-11-monitoring-cost-alerting-rollback--emergency-shutdown)
6. [Acceptance Criteria & Verification Status Matrix](#6-acceptance-criteria--verification-status-matrix)

---

## 1. Target Architecture & Data Flow

```
                                              +-------------------------------------------------------------+
                                              |                     AWS ap-south-1                          |
                                              |                                                             |
+------------------------------+              |  +--------------------+      +---------------------------+  |
|   Streamlit Community Cloud  |              |  | Lambda Function URL|      | AWS Systems Manager (SSM) |  |
|      (Public Internet)       |              |  |  (AuthType = NONE) |      |      Parameter Store      |  |
|                              |              |  +---------+----------+      | /sql-analytics/production |  |
|  - Host: *.streamlit.app     |              |            | HTTPS           |      /config              |  |
|  - Python 3.13 Streamlit UI  |              |            v                 +-------------+-------------+  |
|  - Injects X-Copilot-Secret  +------------->+  +---------+---------------+               |                |
|  - Resolves API_BASE_URL     | HTTPS        |  | AWS Lambda (Container)  |               | (Cold Start    |
+------------------------------+              |  | sql-analytics-api       |<--------------+ Decryption)    |
                                              |  | - Mangum (lifespan=off) |                                |
                                              |  | - FastAPI Application   |                                |
                                              |  | - NullPool SQLAlchemy   |                                |
                                              |  | - AST Guardrails        |                                |
                                              |  +---+-----------------+---+                                |
                                              +------|-----------------|------------------------------------+
                                                     |                 |
                                      PostgreSQL TLS | (port 5432)     | HTTPS (REST API)
                                      sslmode=require|                 v
                                                     v           +-----+----------------------+
                                      +--------------+--------+  |   Google Gemini API        |
                                      |   Neon PostgreSQL     |  |   (gemini-3.1-flash-lite)  |
                                      |   Cloud Database      |  +----------------------------+
                                      |   - analytics schema  |
                                      |   - read-only role    |
                                      |   - 3000ms timeout    |
                                      +-----------------------+
```

### Component Architecture Matrix
| Component | Technology | Configuration & Responsibility |
| :--- | :--- | :--- |
| **Frontend** | Streamlit Community Cloud | Hosted web workspace; sends queries to Lambda Function URL with `X-Copilot-Secret` header. |
| **API Gateway** | Lambda Function URL | Native HTTPS entrypoint (`AuthType=NONE`), protected by application-level shared secret. |
| **Backend Compute** | AWS Lambda (Container) | Container image running Python 3.13, FastAPI, Mangum, and SQLGlot AST guardrails in `ap-south-1`. |
| **Registry** | Amazon ECR (Private) | Stores immutable container images (`sql-analytics-api:latest`). |
| **Secrets & Config** | AWS SSM Parameter Store | Single JSON `SecureString` (`/sql-analytics/production/config`) encrypted via `alias/aws/ssm`. |
| **Database** | Neon Serverless PostgreSQL | Cloud PostgreSQL hosting `analytics` and `app_metadata` schemas with strict role isolation. |
| **AI Engine** | Google Gemini API | Model `gemini-3.1-flash-lite` generating SQL under rigid schema-grounded system prompts. |

---

## 2. Security, Governance & Abuse-Resistance Architecture

### 1. Application-Layer Shared-Secret (`X-Copilot-Secret`)
- **Enforcement:** Enforced on `POST /api/v1/analytics/query` and `GET /health/ready` via [src/api/auth.py](file:///d:/AI%20SQL%20ANALYTICS/src/api/auth.py).
- **Constant-Time Comparison:** Evaluated using `secrets.compare_digest(provided_secret, expected_secret)` to protect against timing-oracle side-channel attacks.
- **Fail-Fast Route Ordering:** In [src/api/routes.py](file:///d:/AI%20SQL%20ANALYTICS/src/api/routes.py#L33), `verify_copilot_secret` is evaluated **before** `check_rate_limit`. Unauthenticated requests are halted immediately with `401 Unauthorized` without consuming rate-limiting bucket state or executing database/LLM calls.
- **Secret Minimum Length:** [validate_production_invariants()](file:///d:/AI%20SQL%20ANALYTICS/src/core/config.py#L131) enforces that `COPILOT_API_SECRET` must be at least 16 characters in production.

### 2. Route Hardening in Production
- **Interactive Documentation Disabled:** `/docs`, `/redoc`, and `/openapi.json` return `404 Not Found` in production, preventing automated schema harvesting.
- **Public Liveness:** `GET /health` remains a lightweight public endpoint (returns `{"status": "healthy"}`) without database overhead.
- **Protected Readiness:** `GET /health/ready` requires `X-Copilot-Secret` in production, preventing unauthorized attackers from causing database connection spikes by spamming readiness probes.

### 3. Payload & Prompt Guardrails
- **Request Body Ceiling:** [RequestBodyLimitMiddleware](file:///d:/AI%20SQL%20ANALYTICS/src/api/middleware.py#L80) rejects requests exceeding 64 KB (`MAX_REQUEST_BODY_BYTES = 65536`) with `413 Payload Too Large`.
- **Question Bounds:** Request models reject questions under 3 characters (or whitespace-only) and questions exceeding 500 characters with `422 Unprocessable Entity`.
- **In-Memory Rate Limiting:** [InMemoryRateLimiter](file:///d:/AI%20SQL%20ANALYTICS/src/api/rate_limiter.py) limits requests per client IP (default 60 requests/minute) with TTL eviction.

### 4. Database Security & AST SQL Guardrails
- **SQLGlot AST Validator:** Every query generated by Gemini is inspected by [SQLSecurityValidator](file:///d:/AI%20SQL%20ANALYTICS/src/security/validator.py). It enforces:
  - Strict `SELECT` statements only (rejects `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `SELECT INTO`).
  - Forbids schema access outside `analytics` (rejects `app_metadata`, `pg_catalog`, `information_schema`).
  - Forbids statement chaining (semicolons separating queries).
  - Forbids SQL comments (`--`, `/* */`).
  - Forbids dangerous PostgreSQL functions (`set_config`, `pg_sleep`, `pg_read_file`, `dblink`, `pg_terminate_backend`).
  - Forces row limits (`LIMIT 1000`).
- **Restricted Database Role:** Executed strictly under the `analytics_user` role, which possesses read-only table and column grants on `analytics.*` and is barred from `app_metadata.*`.
- **Statement Timeout:** Neon enforces `statement_timeout = 3000ms` via engine connection arguments.
- **Serverless Connection Management:** `NullPool` is enforced (`DB_SERVERLESS_MODE = true`), ensuring connections close immediately after query completion and avoiding connection leaks across Lambda container instances.

### 5. Sensitive Data Redaction in Logging
- [SensitiveDataFilter](file:///d:/AI%20SQL%20ANALYTICS/src/core/logging.py#L13) scrubs outgoing CloudWatch log streams of:
  - Database connection strings containing passwords (`://user:***@host`).
  - Gemini API keys (`[REDACTED_API_KEY]`).
  - Bearer tokens (`[REDACTED_TOKEN]`).
  - Customer email addresses (`[REDACTED_EMAIL]`).
  - Incoming `X-Copilot-Secret` headers (`[REDACTED_SECRET]`).

---

## 3. IAM, KMS & Permissions Matrix

### 1. Distinction of Roles
- **Deployment User / Operator:** The human or CI/CD identity running the AWS CLI. Requires administrative permissions to create ECR repositories, push container images, create IAM roles, write SSM parameters, and configure Lambda functions.
- **Lambda Execution Role:** The minimal runtime identity assumed by the AWS Lambda service (`lambda.amazonaws.com`). Requires **zero administrative permissions**; strictly bounded to logging and reading its designated configuration parameter.

### 2. Distinction of AWS KMS Key Types
- **Customer Managed Key (CMK):** Incurs $1.00/month key storage fee. Allows custom resource-based key policies. An explicit `kms:Decrypt` permission in the IAM execution role is strictly mandatory.
- **AWS-Managed Key (`alias/aws/ssm`):** Incurs **$0.00/month key storage fee**. The key policy is managed by AWS and grants access to account principals. 
  - *Documented AWS Syntax Rule:* You **cannot** put `"Resource": "arn:aws:kms:...:alias/aws/ssm"` in an IAM identity-based policy; AWS KMS evaluates alias ARNs as pointing to the alias object, not the key, resulting in `AccessDeniedException`.
  - *Documented Scoping Patterns:* To grant `kms:Decrypt` for `alias/aws/ssm`, you must either:
    1. Resolve the actual underlying KMS Key UUID via `aws kms describe-key --key-id alias/aws/ssm` and specify `"Resource": "arn:aws:kms:ap-south-1:<ACCOUNT_ID>:key/<KEY_UUID>"`.
    2. Use `"Resource": "*"` restricted by `"Condition": {"StringEquals": {"kms:ViaService": "ssm.ap-south-1.amazonaws.com"}}`.

### 3. Minimal Lambda Execution Role Policy
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchLogStreams",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:ap-south-1:<ACCOUNT_ID>:log-group:/aws/lambda/sql-analytics-api:*"
    },
    {
      "Sid": "ReadProductionSSMParameter",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter"
      ],
      "Resource": "arn:aws:ssm:ap-south-1:<ACCOUNT_ID>:parameter/sql-analytics/production/config"
    },
    {
      "Sid": "DecryptSSMViaKMS",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "ssm.ap-south-1.amazonaws.com"
        }
      }
    }
  ]
}
```

---

## 4. Cost Estimation & Operational Planning

### Pricing Basis (`ap-south-1` Mumbai & Google AI)
- **AWS Lambda:** 1,000,000 requests/month and 400,000 GB-seconds/month are permanently free. Above free tier: $0.20/1M requests, $0.0000166667 per GB-second (x86_64).
- **Amazon ECR:** 500 MB/month free for 12 months for new accounts. Standard rate: $0.10/GB-month. Internal data transfer to Lambda in `ap-south-1` is $0.00.
- **AWS SSM Parameter Store:** Standard parameters are $0.00 storage and $0.00 API calls.
- **AWS KMS (`alias/aws/ssm`):** $0.00 monthly key fee. First 20,000 requests/month free across account; $0.03/10,000 requests thereafter.
- **CloudWatch Logs:** 5 GB log ingestion and 5 GB storage free permanently. $0.50/GB ingestion thereafter.
- **Neon PostgreSQL:** 0.5 GiB storage and included compute hours on Free tier plan.
- **Streamlit Community Cloud:** Free hosting for public GitHub repositories.
- **Google Gemini API (`gemini-3.1-flash-lite`):** Free tier available in Google AI Studio. Paid tier: $0.25/1M input tokens, $1.50/1M output tokens.

### Monthly Cost Projections

| Component | Light Traffic (~1,000 Queries/mo) | Moderate Traffic (~50,000 Queries/mo) |
| :--- | :--- | :--- |
| **AWS Lambda Compute** | 1,000 invocations × 1.5s × 512MB = 750 GB-s (**$0.00 - Free Tier**) | 50,000 invocations × 1.5s × 512MB = 37,500 GB-s (**$0.00 - Free Tier**) |
| **AWS Lambda Requests** | 1,000 requests (**$0.00 - Free Tier**) | 50,000 requests (**$0.00 - Free Tier**) |
| **Amazon ECR Storage** | ~520 MB compressed = **$0.00 to $0.05/mo** | ~520 MB compressed = **$0.00 to $0.05/mo** |
| **AWS SSM & KMS** | ~10 cold starts = 10 API calls (**$0.00**) | ~500 cold starts = 500 API calls (**$0.00**) |
| **CloudWatch Logs** | ~5 MB logs (**$0.00 - Free Tier**) | ~250 MB logs (**$0.00 - Free Tier**) |
| **Neon PostgreSQL** | Within Free tier quota (**$0.00**) | Within Free tier quota (**$0.00**) |
| **Streamlit Cloud** | Free hosting (**$0.00**) | Free hosting (**$0.00**) |
| **Gemini API** | ~500k input + 150k output tokens = **~$0.35/mo** (or $0.00 on free tier) | ~25M input + 7.5M output tokens = **~$17.50/mo** |
| **Total Estimated Cost**| **~$0.05 to $0.40 / month** | **~$17.55 to $20.00 / month** |

### Operational Reality of Cost & Abuse Controls
1. **Reserved Concurrency is NOT a Financial Cap:** Setting reserved concurrency to 3 guarantees that at most 3 instances run simultaneously, protecting Neon database connections from connection-pool starvation. However, sequential requests under sustained attack will continuously execute across those 3 instances and accrue duration charges.
2. **AWS Budget Alerts are NOT Spending Caps:** Budget alerts send email/SNS notifications when forecasted or actual spend crosses a threshold (e.g., $2.00). **They do not freeze resources or stop incoming traffic.**
3. **Log Retention Limits:** Explicitly configuring 7-day log retention prevents log storage from silently accumulating indefinite charges.
4. **Emergency Shutdown:** The runbook defines an immediate one-command shutdown procedure (setting reserved concurrency to 0 or revoking the Function URL) to instantly freeze traffic during an incident.

---

## 5. Step-by-Step Deployment Stages

> [!IMPORTANT]
> All commands below are PowerShell-compatible for Windows. Replace all placeholder values (`<ACCOUNT_ID>`, `<SECRET>`, etc.) with your actual identifiers. Never commit or paste real secrets into shell history or source files.

---

### Stage 1: Local Pre-Deployment Verification & Clean Git Setup

#### Purpose & Prerequisites
Verify that the codebase passes all unit and security tests, verify `.gitignore` excludes secret files, and initialize a clean Git repository for deployment.

#### Commands (PowerShell)
```powershell
# 1. Run complete pytest test suite (308 passing tests)
.venv\Scripts\python.exe -m pytest tests/

# 2. Check Git status and verify no .env files are tracked
git status

# 3. If git is not yet initialized:
git init
git add .gitignore .dockerignore README.md pyproject.toml requirements.txt Dockerfile Dockerfile.lambda alembic.ini alembic/ docker/ src/ tests/ AWS_DEPLOYMENT_RUNBOOK.md
git status
```

#### Verification Criteria
- Pytest reports **308 passed** (0 failures, 2 benign third-party deprecation warnings).
- `git status` shows `.env`, `.env.neon`, `.env.*`, and `.venv/` are strictly untracked and excluded by `.gitignore`.

#### Failure Recovery
- If any test fails, resolve the regression locally before proceeding.
- If a `.env` file appears in `git status`, run `git rm --cached <file>` immediately and inspect `.gitignore`.

#### Checkpoint 1
🛑 **STOP & VERIFY:** Ensure zero secret files or local development `.env` files are staged in Git before proceeding.

---

### Stage 2: AWS Account, CLI & Identity Verification

#### Purpose & Prerequisites
Verify that AWS CLI is installed, authenticated with valid deployment credentials, and targeting `ap-south-1` (Mumbai).

#### Commands (PowerShell)
```powershell
# Verify CLI installation
aws --version

# Verify authenticated identity
aws sts get-caller-identity

# Confirm target region
aws configure get region
```

#### Verification Criteria
- Output returns valid AWS Account ID and IAM User/Role ARN:
```json
{
    "UserId": "AIDAXXXXXXXXXXXXXXXXX",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/deployer"
}
```
- Region output confirms `ap-south-1`.

#### Failure Recovery
- If `ExpiredToken` or `InvalidClientTokenId`: Run `aws configure` and supply fresh AWS Access Key ID, Secret Access Key, and region `ap-south-1`.

#### Checkpoint 2
🛑 **STOP & VERIFY:** Confirm the AWS Account ID is correct and you have permission to manage IAM, SSM, ECR, and Lambda.

---

### Stage 3: IAM Lambda Execution Role Creation

#### Purpose & Prerequisites
Create a minimal IAM execution role (`sql-analytics-lambda-exec-role`) assumed by AWS Lambda with access to CloudWatch Logs, SSM Parameter Store, and KMS decryption.

#### Commands (PowerShell)
```powershell
# 1. Create trust policy file locally in temporary directory
$TrustPolicyPath = Join-Path $env:TEMP "lambda-trust-policy.json"
$TrustPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
"@
$TrustPolicy | Set-Content -Path $TrustPolicyPath -Encoding ascii

# 2. Create the IAM Role
aws iam create-role `
  --role-name sql-analytics-lambda-exec-role `
  --assume-role-policy-document "file://$TrustPolicyPath" `
  --description "Minimal execution role for AI SQL Analytics Copilot Lambda"

# 3. Create inline execution policy file
$AccountID = (aws sts get-caller-identity --query Account --output text)
$ExecPolicyPath = Join-Path $env:TEMP "lambda-exec-policy.json"
$ExecPolicy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:ap-south-1:$AccountID:log-group:/aws/lambda/sql-analytics-api:*"
    },
    {
      "Sid": "ReadProductionSSMParameter",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter"
      ],
      "Resource": "arn:aws:ssm:ap-south-1:$AccountID:parameter/sql-analytics/production/config"
    },
    {
      "Sid": "DecryptSSMViaKMS",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "ssm.ap-south-1.amazonaws.com"
        }
      }
    }
  ]
}
"@
$ExecPolicy | Set-Content -Path $ExecPolicyPath -Encoding ascii

# 4. Attach policy to role
aws iam put-role-policy `
  --role-name sql-analytics-lambda-exec-role `
  --policy-name sql-analytics-execution-policy `
  --policy-document "file://$ExecPolicyPath"

# Clean up temporary policy files
Remove-Item $TrustPolicyPath, $ExecPolicyPath
```

#### Verification Criteria
- `aws iam get-role --role-name sql-analytics-lambda-exec-role` returns role ARN `arn:aws:iam::<ACCOUNT_ID>:role/sql-analytics-lambda-exec-role`.

#### Checkpoint 3
🛑 **STOP & VERIFY:** Ensure the IAM role exists and policy is attached before proceeding.

---

### Stage 4: Secure Production Secret Storage in SSM Parameter Store

#### Purpose & Prerequisites
Generate a high-entropy 32-character shared secret and store production configuration as a `SecureString` JSON payload in AWS Systems Manager Parameter Store under `/sql-analytics/production/config`.

> [!NOTE]
> Writing to a temporary file via `Set-Content` and passing `file://$TempConfigPath` guarantees that Windows PowerShell will not strip double quotes from the JSON string, and prevents secrets from leaking into process listings or shell history.

#### Commands (PowerShell)
```powershell
# 1. Generate high-entropy 32-character shared secret in PowerShell
$SharedSecret = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 32 | ForEach-Object {[char]$_})
Write-Host "Generated shared secret: $SharedSecret"
# SAVE THIS SECRET SECURELY IN YOUR PASSWORD MANAGER FOR STREAMLIT CONFIGURATION!

# 2. Construct production configuration JSON payload
# Fill in your actual Neon credentials and Gemini API key below:
$ConfigHash = @{
    ENVIRONMENT = "production"
    POSTGRES_HOST = "<YOUR_NEON_HOST_SUBDOMAIN>.ap-south-1.aws.neon.tech"
    POSTGRES_PORT = 5432
    POSTGRES_DB = "neondb"
    APP_DB_USER = "app_user"
    APP_DB_PASSWORD = "<YOUR_NEON_APP_USER_PASSWORD>"
    ANALYTICS_DB_USER = "analytics_user"
    ANALYTICS_DB_PASSWORD = "<YOUR_NEON_ANALYTICS_USER_PASSWORD>"
    PGSSLMODE = "require"
    DB_SERVERLESS_MODE = $true
    COPILOT_API_SECRET = $SharedSecret
    GEMINI_API_KEY = "<YOUR_GOOGLE_GEMINI_API_KEY>"
    GEMINI_MODEL = "gemini-3.1-flash-lite"
    CORS_ORIGINS = "https://share.streamlit.io,https://<YOUR_STREAMLIT_APP_NAME>.streamlit.app"
    LOG_LEVEL = "INFO"
    LOG_FORMAT = "json"
}
$ConfigJson = $ConfigHash | ConvertTo-Json -Compress

# 3. Write securely to temporary file
$TempConfigPath = Join-Path $env:TEMP "ssm-config-payload.json"
$ConfigJson | Set-Content -Path $TempConfigPath -Encoding utf8

# 4. Store parameter in SSM Parameter Store
aws ssm put-parameter `
  --name "/sql-analytics/production/config" `
  --type "SecureString" `
  --key-id "alias/aws/ssm" `
  --value "file://$TempConfigPath" `
  --description "Production configuration for AI SQL Analytics Copilot" `
  --overwrite

# 5. Clean up temporary payload file and clear memory
Remove-Item $TempConfigPath
$ConfigHash = $null
$ConfigJson = $null

# 6. Verify parameter exists without printing secret value
aws ssm get-parameter `
  --name "/sql-analytics/production/config" `
  --query "Parameter.[Name,Type,Version]" `
  --output table
```

#### Verification Criteria
- Table output confirms Name `/sql-analytics/production/config`, Type `SecureString`, and Version >= 1.
- No secret values are printed in console logs.

#### Failure Recovery
- If `ParameterAlreadyExists` and `--overwrite` was omitted, include `--overwrite`.
- If `AccessDeniedException`: Confirm user has `ssm:PutParameter`.

#### Checkpoint 4
🛑 **STOP & VERIFY:** Parameter is stored and verified.

---

### Stage 5: Amazon ECR Private Repository & Image Publishing

#### Purpose & Prerequisites
Create a private ECR repository, build the production Lambda container image, tag it, and push it to ECR.

#### Commands (PowerShell)
```powershell
$AccountID = (aws sts get-caller-identity --query Account --output text)
$Region = "ap-south-1"
$RepoName = "sql-analytics-api"
$ImageUri = "$AccountID.dkr.ecr.$Region.amazonaws.com/${RepoName}:latest"

# 1. Create ECR repository with scan-on-push enabled
aws ecr create-repository `
  --repository-name $RepoName `
  --image-scanning-configuration scanOnPush=true `
  --encryption-configuration encryptionType=AES256 `
  --region $Region

# 2. Authenticate Docker with Amazon ECR
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin "$AccountID.dkr.ecr.$Region.amazonaws.com"

# 3. Build local production image from Dockerfile.lambda
docker build -f Dockerfile.lambda -t "${RepoName}:latest" .

# 4. Tag image for ECR
docker tag "${RepoName}:latest" $ImageUri

# 5. Push container image to ECR
docker push $ImageUri

# 6. Verify image published in ECR
aws ecr describe-images `
  --repository-name $RepoName `
  --query "imageDetails[0].[imageDigest,imageSizeInBytes,imagePushedAt]" `
  --output table
```

#### Verification Criteria
- `docker push` completes with sha256 manifest digest.
- `describe-images` returns image digest and size (~520 MB compressed).

#### Checkpoint 5
🛑 **STOP & VERIFY:** Image is published in ECR and verified before deploying Lambda.

---

### Stage 6: AWS Lambda Function Creation & Serverless Configuration

#### Purpose & Prerequisites
Create the AWS Lambda function from the ECR image, configure memory (512 MB), timeout (45s), environment variables, and reserved concurrency (3).

#### Commands (PowerShell)
```powershell
$AccountID = (aws sts get-caller-identity --query Account --output text)
$Region = "ap-south-1"
$RoleArn = "arn:aws:iam::$AccountID:role/sql-analytics-lambda-exec-role"
$ImageUri = "$AccountID.dkr.ecr.$Region.amazonaws.com/sql-analytics-api:latest"

# 1. Create Lambda function
aws lambda create-function `
  --function-name sql-analytics-api `
  --package-type Image `
  --code ImageUri=$ImageUri `
  --role $RoleArn `
  --timeout 45 `
  --memory-size 512 `
  --environment "Variables={SSM_CONFIG_PARAMETER_NAME=/sql-analytics/production/config,ENVIRONMENT=production,DB_SERVERLESS_MODE=true}" `
  --region $Region

# 2. Wait for Lambda function to become Active
aws lambda wait function-active-v2 --function-name sql-analytics-api

# 3. Set Reserved Concurrency to 3 (downstream Neon database protection)
aws lambda put-function-concurrency `
  --function-name sql-analytics-api `
  --reserved-concurrent-executions 3

# 4. Set CloudWatch Log Group retention to 7 days
aws logs put-retention-policy `
  --log-group-name "/aws/lambda/sql-analytics-api" `
  --retention-in-days 7
```

#### Verification Criteria
- `aws lambda get-function --function-name sql-analytics-api --query "Configuration.[State,LastUpdateStatus]"` returns `Active, Successful`.
- Reserved concurrency confirms `{"ReservedConcurrentExecutions": 3}`.

#### Checkpoint 6
🛑 **STOP & VERIFY:** Function is Active and concurrency is configured.

---

### Stage 7: Lambda Function URL & Shared-Secret Auth Verification

#### Purpose & Prerequisites
Create a publicly addressable Lambda Function URL with `AuthType=NONE`, configure CORS, and immediately verify that unauthenticated calls are rejected with HTTP 401.

#### Commands (PowerShell)
```powershell
# 1. Create Function URL (AuthType=NONE) with CORS using unambiguous JSON syntax
aws lambda create-function-url-config `
  --function-name sql-analytics-api `
  --auth-type NONE `
  --cors '{\"AllowOrigins\":[\"https://share.streamlit.io\",\"https://*.streamlit.app\"],\"AllowMethods\":[\"GET\",\"POST\",\"OPTIONS\"],\"AllowHeaders\":[\"content-type\",\"x-copilot-secret\"],\"MaxAge\":300}'

# 2. Add resource-based permission allowing public URL invocation
aws lambda add-permission `
  --function-name sql-analytics-api `
  --statement-id FunctionURLAllowPublicAccess `
  --action lambda:InvokeFunctionUrl `
  --principal "*" `
  --function-url-auth-type NONE

# 3. Retrieve assigned Function URL
$FunctionUrl = (aws lambda get-function-url-config --function-name sql-analytics-api --query "FunctionUrl" --output text)
Write-Host "Assigned Lambda Function URL: $FunctionUrl"

# 4. Test 1: Public Liveness Check (/health) -> Expected: 200 OK
curl.exe -s -i "$FunctionUrl/health"

# 5. Test 2: Unauthenticated Readiness (/health/ready) -> Expected: 401 Unauthorized
curl.exe -s -i "$FunctionUrl/health/ready"

# 6. Test 3: Unauthenticated Query (/api/v1/analytics/query) -> Expected: 401 Unauthorized
curl.exe -s -i -X POST "$FunctionUrl/api/v1/analytics/query" `
  -H "Content-Type: application/json" `
  -d '{\"question\":\"What is our total revenue?\"}'

# 7. Test 4: Authenticated Query with Valid Secret -> Expected: 200 OK
curl.exe -s -i -X POST "$FunctionUrl/api/v1/analytics/query" `
  -H "Content-Type: application/json" `
  -H "X-Copilot-Secret: $SharedSecret" `
  -d '{\"question\":\"Which product generated the highest revenue?\"}'
```

#### Verification Criteria
- `GET /health` returns `HTTP/1.1 200 OK` with `{"status":"healthy"}`.
- `GET /health/ready` without header returns `HTTP/1.1 401 Unauthorized`.
- `POST /api/v1/analytics/query` without header returns `HTTP/1.1 401 Unauthorized`.
- `POST /api/v1/analytics/query` with `X-Copilot-Secret` returns `HTTP/1.1 200 OK` with valid SQL, explanation, and query results.

#### Checkpoint 7
🛑 **STOP & VERIFY:** Function URL rejects unauthenticated traffic and admits valid shared-secret requests.

---

### Stage 8: Lambda Networking, Neon TLS Connectivity & Pool Lifecycle

#### Purpose & Prerequisites
Verify that the Lambda function successfully connects to Neon PostgreSQL over TLS (`sslmode=require`) and that `NullPool` is operating as expected without connection leaks.

#### Commands (PowerShell)
```powershell
# 1. Invoke readiness check with authentication
curl.exe -s -H "X-Copilot-Secret: $SharedSecret" "$FunctionUrl/health/ready"

# 2. Inspect latest CloudWatch logs to verify TLS and NullPool
$LogGroupName = "/aws/lambda/sql-analytics-api"
aws logs tail $LogGroupName --since 10m
```

#### Verification Criteria
- Readiness output returns `{"status":"ready","database":"connected","llm_configured":true}`.
- CloudWatch logs show structured JSON logs with `"level": "INFO"` and zero leaked database passwords or API keys.

---

### Stage 9: Streamlit Community Cloud Deployment & Integration

#### Purpose & Prerequisites
Deploy the frontend to Streamlit Community Cloud, linking to your GitHub repository and injecting `API_BASE_URL` and `BACKEND_API_SECRET`.

#### Steps
1. **Push Clean Git Repository to GitHub:**
   ```powershell
   git remote add origin https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPO_NAME>.git
   git branch -M main
   git push -u origin main
   ```
2. **Deploy on Streamlit Community Cloud:**
   - Log in to [share.streamlit.io](https://share.streamlit.io).
   - Click **New app**.
   - Select your repository and branch (`main`).
   - Set **Main file path** to `src/frontend/app.py`.
3. **Configure Streamlit Secrets in Dashboard:**
   In **App settings -> Secrets**, enter:
   ```toml
   API_BASE_URL = "https://<UNIQUE_ID>.lambda-url.ap-south-1.on.aws"
   BACKEND_API_SECRET = "<YOUR_32_CHAR_SHARED_SECRET>"
   ```
   Click **Save**.
4. **Update SSM Parameter CORS Origins:**
   Once your Streamlit app URL is assigned (e.g. `https://my-copilot-app.streamlit.app`), execute this PowerShell script to update `CORS_ORIGINS` in SSM without retyping the entire payload:
   ```powershell
   # 1. Fetch existing SSM JSON parameter value
   $CurrentConfig = (aws ssm get-parameter --name "/sql-analytics/production/config" --with-decryption --query "Parameter.Value" --output text | ConvertFrom-Json)

   # 2. Add exact assigned Streamlit URL to CORS_ORIGINS
   $StreamlitAppUrl = "https://<YOUR_ACTUAL_STREAMLIT_APP_NAME>.streamlit.app"
   $CurrentConfig.CORS_ORIGINS = "https://share.streamlit.io,$StreamlitAppUrl"

   # 3. Save updated configuration back to SSM Parameter Store safely via temporary file
   $UpdatedJson = $CurrentConfig | ConvertTo-Json -Compress
   $TempPath = Join-Path $env:TEMP "ssm-cors-update.json"
   $UpdatedJson | Set-Content -Path $TempPath -Encoding utf8

   aws ssm put-parameter `
     --name "/sql-analytics/production/config" `
     --type "SecureString" `
     --key-id "alias/aws/ssm" `
     --value "file://$TempPath" `
     --overwrite

   Remove-Item $TempPath
   ```

#### Verification Criteria
- Streamlit application loads without errors, showing the dark-mode analytics workspace.
- KPI cards, sidebar health status, and AI Copilot chat panel display `Backend: Connected (v0.1.0)`.

---

### Stage 10: End-to-End Functional, Security & Guardrail Verification

#### Purpose & Prerequisites
Execute comprehensive security and functional tests against the live deployment to confirm AST guardrails, column security, timeouts, and row limits.

#### Verification Test Suite
1. **Canonical Business Query:**
   - Ask: `"Which product generated the highest revenue?"`
   - Expected: Returns status `ready`, valid SQL querying `analytics.products` and `analytics.order_items`, KPI cards render, and Plotly chart displays.
2. **SQL Injection Defense:**
   - Submit malicious prompt: `"Show products; DROP TABLE analytics.products;"`
   - Expected: AST Validator intercepts query; status returns `unsupported` or error; table is untouched.
3. **Restricted Schema Access:**
   - Submit prompt: `"Show all records from app_metadata.query_audit_logs"`
   - Expected: Rejected with security policy violation (unapproved schema).
4. **Statement Timeout Enforcement:**
   - Neon enforces 3000ms query timeout; any long-running query is cancelled by the database.
5. **Row Clamp Enforcement:**
   - Queries returning massive datasets are clamped at 1,000 rows (`MAX_QUERY_ROW_LIMIT = 1000`).

---

### Stage 11: Monitoring, Cost Alerting, Rollback & Emergency Shutdown

#### 1. Configure AWS Monthly Budget Alert ($2.00)
```powershell
$AccountID = (aws sts get-caller-identity --query Account --output text)
$BudgetPath = Join-Path $env:TEMP "budget.json"
$NotificationsPath = Join-Path $env:TEMP "budget-notifications.json"

$BudgetJson = @"
{
  "BudgetLimit": {
    "Amount": "2.00",
    "Unit": "USD"
  },
  "BudgetName": "sql-analytics-monthly-cap",
  "BudgetType": "COST",
  "CostTypes": {
    "IncludeTax": true,
    "IncludeSubscription": true,
    "UseBlended": false,
    "IncludeRefund": false,
    "IncludeCredit": false,
    "IncludeUpfront": true,
    "IncludeRecurring": true,
    "IncludeOtherSubscription": true,
    "IncludeSupport": true,
    "IncludeDiscount": true,
    "UseAmortized": false
  },
  "TimeUnit": "MONTHLY"
}
"@
$BudgetJson | Set-Content -Path $BudgetPath -Encoding ascii

$NotificationsJson = @"
[
  {
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80,
      "ThresholdType": "PERCENTAGE"
    },
    "Subscribers": [
      {
        "SubscriptionType": "EMAIL",
        "Address": "<YOUR_EMAIL@EXAMPLE.COM>"
      }
    ]
  }
]
"@
$NotificationsJson | Set-Content -Path $NotificationsPath -Encoding ascii

aws budgets create-budget `
  --account-id $AccountID `
  --budget "file://$BudgetPath" `
  --notifications-with-subscribers "file://$NotificationsPath"

Remove-Item $BudgetPath, $NotificationsPath
```

#### 2. Emergency Instant Shutdown Procedure (Under Incident / Attack)
To immediately block all incoming traffic and halt execution:
```powershell
# Method A: Set Reserved Concurrency to 0 (Throttles 100% of invocations immediately)
aws lambda put-function-concurrency `
  --function-name sql-analytics-api `
  --reserved-concurrent-executions 0

# Method B: Delete Function URL (Permanently disables public HTTP endpoint)
aws lambda delete-function-url-config `
  --function-name sql-analytics-api
```

#### 3. Service Restoration Procedure (Post-Incident)
Once the incident is resolved and you are ready to restore traffic:
```powershell
# If Method A was used (Reserved Concurrency set to 0):
aws lambda put-function-concurrency `
  --function-name sql-analytics-api `
  --reserved-concurrent-executions 3

# If Method B was used (Function URL deleted):
# Recreate Function URL and re-attach invoke permission:
aws lambda create-function-url-config `
  --function-name sql-analytics-api `
  --auth-type NONE `
  --cors '{\"AllowOrigins\":[\"https://share.streamlit.io\",\"https://*.streamlit.app\"],\"AllowMethods\":[\"GET\",\"POST\",\"OPTIONS\"],\"AllowHeaders\":[\"content-type\",\"x-copilot-secret\"],\"MaxAge\":300}'

aws lambda add-permission `
  --function-name sql-analytics-api `
  --statement-id FunctionURLAllowPublicAccess `
  --action lambda:InvokeFunctionUrl `
  --principal "*" `
  --function-url-auth-type NONE
```

#### 4. Image Rollback Procedure
If a bad container image is deployed:
```powershell
# Point Lambda back to previous stable image digest or tag
aws lambda update-function-code `
  --function-name sql-analytics-api `
  --image-uri "$AccountID.dkr.ecr.ap-south-1.amazonaws.com/sql-analytics-api:<PREVIOUS_TAG_OR_DIGEST>"
```

#### 5. Shared Secret Rotation Procedure
If `COPILOT_API_SECRET` is compromised:
1. Generate new 32-character secret in PowerShell.
2. Update `/sql-analytics/production/config` in SSM Parameter Store with new secret.
3. Update `BACKEND_API_SECRET` in Streamlit Community Cloud settings.
4. Force Lambda container recycle:
   ```powershell
   aws lambda update-function-configuration --function-name sql-analytics-api --description "Secret rotated at $(Get-Date -Format s)"
   ```

---

## 6. Acceptance Criteria & Verification Status Matrix

| Check / Requirement | Local Test Result | Live AWS Status | Verification Method |
| :--- | :--- | :--- | :--- |
| **Complete Unit Test Suite** | **PASS (308/308 passed)** | N/A | Pytest execution locally |
| **Fail-Closed Production Invariants** | **PASS** | Pending Provisioning | Verified in unit & container tests |
| **No .env Fallback in Production** | **PASS** | Pending Provisioning | Tested inside Docker container |
| **Boto3 SDK Included in Container** | **PASS (v1.42.97 in image)**| Pending Provisioning | Verified in container smoke test |
| **Local Container Smoke Tests** | **PASS (All 10 checks passed)**| N/A | Executed in `sql-analytics-api:local-audit` using synthetic non-existent DB host (`ep-prod...`) and synthetic dummy passwords (`audit_*`). Zero database contact made. |
| **Unauthenticated Request Rejection** | **PASS (401 Unauthorized)** | Pending Provisioning | Verified in container smoke test |
| **Invalid Secret Rejection** | **PASS (401 Unauthorized)** | Pending Provisioning | Verified in container smoke test |
| **Valid Secret Acceptance** | **PASS (Routes to schema)** | Pending Provisioning | Verified in container smoke test |
| **Interactive Docs Disabled (404)** | **PASS (/docs returns 404)** | Pending Provisioning | Verified in container smoke test |
| **Readiness Probe Protected** | **PASS (401 without secret)**| Pending Provisioning | Verified in container smoke test |
| **AST SQL Security Guardrails** | **PASS** | Pending Provisioning | 70+ security test assertions |
| **Statement Timeout Enforcement** | **PASS (3000ms)** | Pending Provisioning | Verified against local & Neon DB |
| **NullPool Connection Management** | **PASS** | Pending Provisioning | Code & configuration verified |
| **Live SSM Parameter Retrieval** | **MOCKED** | **UNVERIFIED (Cloud)** | Requires live AWS Parameter Store |
| **Live Function URL Endpoint** | **UNVERIFIED** | **UNVERIFIED (Cloud)** | Requires live AWS Lambda creation |
| **Streamlit Cloud Integration** | **UNVERIFIED** | **UNVERIFIED (Cloud)** | Requires GitHub push & Streamlit deploy |
