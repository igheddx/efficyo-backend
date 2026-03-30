import os
from dataclasses import dataclass


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
        )


settings = Settings.from_env()