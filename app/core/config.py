import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv as _load_dotenv
    _env_file = Path(__file__).resolve().parents[2] / ".env"
    if _env_file.is_file():
        _load_dotenv(dotenv_path=_env_file, override=False)
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    aws_region: str
    environment: str
    app_name: str
    debug: bool
    # When true, Tipwave demo cloud account skips sts:AssumeRole and uses the process default credential chain.
    bypass_assume_role_for_tipwave_demo: bool
    # When true, EC2/RDS/Lambda/EBS inventory runs in every commercial region returned by ec2:DescribeRegions
    # (not only cloud_accounts.region_default). Prevents missing resources and understated findings/savings.
    aws_scan_all_regions: bool
    # Session cookie auth
    session_cookie_name: str
    session_ttl_hours: int
    cookie_secure: bool
    # When true, X-User / X-Role headers are accepted (tests and emergency debugging only).
    allow_dev_header_auth: bool
    # Password for seeded dev accounts (root@fptnext.local, demo@fptnext.local, etc.).
    dev_seed_password: str
    # When false, skip local demo users, Tipwave demo tenant/cloud rows, and demo org seeding.
    enable_demo_and_local_seed: bool
    # Production seed user (if set, creates this user as root admin)
    prod_seed_email: str | None
    prod_seed_name: str | None
    prod_seed_password: str | None
    prod_seed_company: str | None
    prod_seed_customer: str | None
    # OIDC (OAuth2 authorization code + OpenID). All four must be set to enable SSO routes.
    oidc_issuer_url: str | None
    oidc_client_id: str | None
    oidc_client_secret: str | None
    oidc_redirect_uri: str | None
    oidc_post_login_redirect: str
    oidc_scopes: str
    # Operations Copilot (optional). When unset, copilot uses a rules-based response from real DB/API data.
    openai_api_key: str | None
    openai_base_url: str | None
    copilot_model: str
    # Transactional email (SES-ready, provider-agnostic defaults).
    email_enabled: bool
    email_provider: str
    ses_region: str
    ses_from_email: str
    ses_from_name: str
    email_sandbox_mode: bool
    email_allowlist: str | None
    # Optional explicit SES credentials (overrides default boto3 credential chain for email only).
    ses_aws_access_key_id: str | None
    ses_aws_secret_access_key: str | None
    frontend_url: str
    api_public_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("ENVIRONMENT", "dev")
        bypass = os.getenv("FPTNEXT_BYPASS_ASSUME_ROLE_FOR_TIPWAVE", "").lower() in ("1", "true", "yes")
        scan_all = os.getenv("FPTNEXT_AWS_SCAN_ALL_REGIONS", "").lower() in ("1", "true", "yes")
        dev_headers = os.getenv(
            "FPTNEXT_DEV_HEADER_AUTH",
            "true" if environment == "dev" else "false",
        ).lower() in ("1", "true", "yes")

        oidc_issuer = os.getenv("FPTNEXT_OIDC_ISSUER_URL", "").strip() or None
        oidc_cid = os.getenv("FPTNEXT_OIDC_CLIENT_ID", "").strip() or None
        oidc_sec = os.getenv("FPTNEXT_OIDC_CLIENT_SECRET", "").strip() or None
        oidc_redir = os.getenv("FPTNEXT_OIDC_REDIRECT_URI", "").strip() or None
        openai_key = os.getenv("FPTNEXT_OPENAI_API_KEY", "").strip() or None
        openai_base = os.getenv("FPTNEXT_OPENAI_BASE_URL", "").strip() or None
        copilot_model = os.getenv("FPTNEXT_COPILOT_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        email_enabled = os.getenv("FPTNEXT_EMAIL_ENABLED", "false").lower() in ("1", "true", "yes")
        email_provider = os.getenv("FPTNEXT_EMAIL_PROVIDER", "ses").strip().lower() or "ses"
        ses_region = (
            os.getenv("FPTNEXT_SES_REGION", "").strip()
            or os.getenv("AWS_REGION", "us-east-1").strip()
            or "us-east-1"
        )
        ses_from_email = os.getenv("FPTNEXT_SES_FROM_EMAIL", "noreply@meezi.io").strip() or "noreply@meezi.io"
        ses_from_name = os.getenv("FPTNEXT_SES_FROM_NAME", "MEEZI").strip() or "MEEZI"
        email_sandbox_mode = os.getenv(
            "FPTNEXT_EMAIL_SANDBOX_MODE",
            "false" if environment == "prod" else "true",
        ).lower() in ("1", "true", "yes")
        email_allowlist = os.getenv("FPTNEXT_EMAIL_ALLOWLIST", "").strip() or None
        ses_aws_access_key_id = os.getenv("FPTNEXT_SES_AWS_ACCESS_KEY_ID", "").strip() or None
        ses_aws_secret_access_key = os.getenv("FPTNEXT_SES_AWS_SECRET_ACCESS_KEY", "").strip() or None
        frontend_url = os.getenv("FPTNEXT_FRONTEND_URL", "http://localhost:5173").strip() or "http://localhost:5173"
        api_public_url = os.getenv("FPTNEXT_API_PUBLIC_URL", "http://127.0.0.1:8000").strip() or "http://127.0.0.1:8000"
        enable_demo_seed = os.getenv("FPTNEXT_ENABLE_DEMO_AND_LOCAL_SEED", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        prod_seed_email = os.getenv("FPTNEXT_PROD_SEED_EMAIL", "").strip() or None
        prod_seed_name = os.getenv("FPTNEXT_PROD_SEED_NAME", "").strip() or None
        prod_seed_password = os.getenv("FPTNEXT_PROD_SEED_PASSWORD", "").strip() or None
        prod_seed_company = os.getenv("FPTNEXT_PROD_SEED_COMPANY", "").strip() or None
        prod_seed_customer = os.getenv("FPTNEXT_PROD_SEED_CUSTOMER", "").strip() or None

        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                # Default matches docker-compose `db` service (postgres:16 on host port 5433).
                "postgresql+psycopg2://optimizer:optimizer@127.0.0.1:5433/optimizer_db",
            ),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            environment=environment,
            app_name=os.getenv("APP_NAME", "optimizer-backend"),
            debug=environment == "dev",
            bypass_assume_role_for_tipwave_demo=bypass,
            aws_scan_all_regions=scan_all,
            session_cookie_name=os.getenv("FPTNEXT_SESSION_COOKIE", "fptnext_session"),
            session_ttl_hours=int(os.getenv("FPTNEXT_SESSION_TTL_HOURS", "168")),
            # Secure cookies are dropped on plain HTTP; never force them in dev even if .env copies prod.
            cookie_secure=(
                os.getenv("FPTNEXT_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
                and environment != "dev"
            ),
            allow_dev_header_auth=dev_headers,
            dev_seed_password=os.getenv("FPTNEXT_DEV_SEED_PASSWORD", "devpassword"),
            enable_demo_and_local_seed=enable_demo_seed,
            prod_seed_email=prod_seed_email,
            prod_seed_name=prod_seed_name,
            prod_seed_password=prod_seed_password,
            prod_seed_company=prod_seed_company,
            prod_seed_customer=prod_seed_customer,
            oidc_issuer_url=oidc_issuer,
            oidc_client_id=oidc_cid,
            oidc_client_secret=oidc_sec,
            oidc_redirect_uri=oidc_redir,
            oidc_post_login_redirect=(
                os.getenv("FPTNEXT_OIDC_POST_LOGIN_REDIRECT", "http://localhost:5173/").strip()
                or "http://localhost:5173/"
            ),
            oidc_scopes=(
                os.getenv("FPTNEXT_OIDC_SCOPES", "openid email profile").strip()
                or "openid email profile"
            ),
            openai_api_key=openai_key,
            openai_base_url=openai_base,
            copilot_model=copilot_model,
            email_enabled=email_enabled,
            email_provider=email_provider,
            ses_region=ses_region,
            ses_from_email=ses_from_email,
            ses_from_name=ses_from_name,
            email_sandbox_mode=email_sandbox_mode,
            email_allowlist=email_allowlist,
            ses_aws_access_key_id=ses_aws_access_key_id,
            ses_aws_secret_access_key=ses_aws_secret_access_key,
            frontend_url=frontend_url,
            api_public_url=api_public_url,
        )


settings = Settings.from_env()