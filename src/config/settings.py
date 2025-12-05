"""
Configuration settings for YouGile MCP server.
Manages environment variables and default values.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """YouGile MCP server configuration."""
    
    # YouGile API settings
    yougile_base_url: str = "https://yougile.com"
    yougile_email: Optional[str] = None
    yougile_password: Optional[str] = None
    yougile_company_id: Optional[str] = None
    yougile_api_key: Optional[str] = None
    
    # HTTP client settings
    yougile_timeout: int = 30
    yougile_max_retries: int = 3
    yougile_rate_limit_per_minute: int = 25
    
    # Local database URL (PostgreSQL by default; override via YOUGILE_LOCAL_DB_URL)
    yougile_local_db_url: str = "postgresql+asyncpg://yougile:yougile@localhost/yougile"
    
    # Webhook server settings (optional)
    yougile_webhook_host: Optional[str] = None
    yougile_webhook_port: Optional[int] = None
    yougile_webhook_public_url: Optional[str] = None
    yougile_webhook_reload: Optional[int] = None
    yougile_webhook_db_url: Optional[str] = None

    # Redmine settings
    redmine_url: Optional[str] = None
    redmine_api_key: Optional[str] = None
    redmine_verify_ssl: bool = True
    redmine_default_password: Optional[str] = None

    # MCP server settings
    server_name: str = "YouGile MCP Server"
    server_version: str = "1.0.0"
    
    # User-configurable context instructions (set via MCP client config)
    user_context: Optional[str] = None
    
    # Development settings
    log_level: str = "INFO"
    
    class Config:
        env_prefix = ""
        env_file = ".env"
        case_sensitive = False


# Find .env file relative to this settings.py file
def find_env_file():
    """Find .env file in project root."""
    current_file = Path(__file__)
    project_root = current_file.parent.parent.parent  # Go up to project root
    env_file = project_root / ".env"
    return str(env_file) if env_file.exists() else None

# Global settings instance with explicit env file path
env_file_path = find_env_file()
if env_file_path:
    settings = Settings(_env_file=env_file_path)
else:
    settings = Settings()