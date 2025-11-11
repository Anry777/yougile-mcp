"""
HTTP client wrapper for YouGile API.
Handles requests, retries, rate limiting, and error handling.
"""

import asyncio
import json
import time
from typing import Optional, Dict, Any, Union
import os
import httpx
from ..config import settings
from .auth import AuthManager
from .exceptions import (
    YouGileError,
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
    NotFoundError,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Raw HTTP debug toggle (set YOUGILE_HTTP_DEBUG=1 to enable)
_HTTP_DEBUG = os.environ.get("YOUGILE_HTTP_DEBUG", "0") in {"1", "true", "True"}

class YouGileClient:
    """HTTP client for YouGile API with built-in error handling and retries."""
    
    def __init__(self, auth_manager: Optional[AuthManager] = None):
        self.auth_manager = auth_manager or AuthManager()
        self.base_url = settings.yougile_base_url
        self._client: Optional[httpx.AsyncClient] = None
        # Simple per-instance throttle to respect configured rate limit
        self._last_request_ts: float = 0.0
    
    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(settings.yougile_timeout),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=100),
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
    
    async def _auto_reinitialize(self):
        """Automatically reinitialize authentication if possible."""
        if all([settings.yougile_email, settings.yougile_password, settings.yougile_company_id]):
            # Import here to avoid circular imports
            from .. import server
            from . import auth
            await server.initialize_auth()
            # Update local auth_manager with global credentials
            if auth.auth_manager.is_authenticated():
                self.auth_manager.set_credentials(
                    auth.auth_manager.api_key, 
                    auth.auth_manager.company_id
                )
    
    async def request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Make an authenticated request to YouGile API."""
        if not self._client:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")
        
        # Use basic headers for auth endpoints, auth headers for others
        if path.startswith("/auth/") or path.startswith("/api-v2/auth/"):
            headers = self.auth_manager.get_basic_headers()
        else:
            # Auto-reinitialize if not authenticated
            if not self.auth_manager.is_authenticated():
                logger.warning("Client not authenticated, attempting auto-reinitialization")
                await self._auto_reinitialize()
            headers = self.auth_manager.get_auth_headers()
            
        full_url = f"/api-v2{path}" if not path.startswith("/api-v2") else path
        
        # Log request details
        logger.debug(f"API Request: {method} {full_url}")
        if params:
            logger.debug(f"  Params: {params}")
        if json:
            # Mask sensitive data in logs
            safe_json = self._mask_sensitive_data(json)
            logger.debug(f"  Body: {safe_json}")
        
        for attempt in range(settings.yougile_max_retries + 1):
            try:
                start_ts = time.monotonic()
                # Throttle based on configured RPM
                try:
                    min_interval = 60.0 / max(1, int(settings.yougile_rate_limit_per_minute))
                except Exception:
                    min_interval = 1.0
                wait = (self._last_request_ts + min_interval) - start_ts
                if wait > 0:
                    await asyncio.sleep(wait)
                # issue request
                response = await self._client.request(
                    method=method,
                    url=full_url,
                    json=json,
                    params=params,
                    headers=headers,
                    **kwargs
                )
                
                logger.debug(f"API Response: {response.status_code} for {method} {full_url}")
                # Handle 429 centrally: enforce fixed 30s cooldown before retry
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    # Fixed cooldown 30s after hitting 429 (stricter than RPM spacing)
                    sleep_sec: float = 30.0
                    # If server recommends longer, respect it
                    if retry_after:
                        try:
                            sleep_sec = max(sleep_sec, float(retry_after))
                        except ValueError:
                            pass
                    logger.warning(
                        f"Rate limited (429). Sleeping {sleep_sec:.2f}s before retry (attempt {attempt + 1}/{settings.yougile_max_retries + 1})"
                    )
                    if attempt == settings.yougile_max_retries:
                        # Last attempt - convert to error via handler to raise RateLimitError
                        result = self._handle_response(response)
                        return result  # unreachable, handler raises
                    await asyncio.sleep(sleep_sec)
                    continue

                result = self._handle_response(response)
                duration_ms = int((time.monotonic() - start_ts) * 1000)
                logger.debug(f"  Response data size: {len(str(result))} chars")
                logger.debug(f"  Request duration: {duration_ms} ms (attempt {attempt + 1})")
                # Mark last request timestamp after successful handling
                self._last_request_ts = time.monotonic()
                return result
                
            except httpx.TimeoutException as e:
                duration_ms = int((time.monotonic() - start_ts) * 1000) if 'start_ts' in locals() else None
                logger.warning(f"Request timeout (attempt {attempt + 1}/{settings.yougile_max_retries + 1}): {method} {full_url} | duration={duration_ms} ms")
                if attempt == settings.yougile_max_retries:
                    logger.error(f"Request timeout after {settings.yougile_max_retries + 1} attempts")
                    raise YouGileError("Request timeout")
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                
            except httpx.NetworkError as e:
                duration_ms = int((time.monotonic() - start_ts) * 1000) if 'start_ts' in locals() else None
                logger.warning(f"Network error (attempt {attempt + 1}/{settings.yougile_max_retries + 1}): {str(e)} | duration={duration_ms} ms")
                if attempt == settings.yougile_max_retries:
                    logger.error(f"Network error after {settings.yougile_max_retries + 1} attempts: {str(e)}")
                    raise YouGileError(f"Network error: {str(e)}")
                await asyncio.sleep(2 ** attempt)
        
        logger.error(f"Max retries exceeded for {method} {full_url}")
        raise YouGileError("Max retries exceeded")
    
    def _mask_sensitive_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Mask sensitive data in logs."""
        if not isinstance(data, dict):
            return data
        
        masked = data.copy()
        sensitive_keys = ['password', 'api_key', 'token', 'secret']
        
        for key in masked:
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                masked[key] = "***MASKED***"
        
        return masked
    
    def _handle_response(self, response: httpx.Response) -> Any:
        """Handle HTTP response, raising appropriate exceptions for errors.
        Returns JSON for successful responses.
        """
        if 200 <= response.status_code < 300:
            if _HTTP_DEBUG:
                try:
                    logger.debug(
                        "HTTP DEBUG success: %s %s -> %s\nHeaders: %s\nBody: %s",
                        response.request.method,
                        response.request.url,
                        response.status_code,
                        dict(response.headers),
                        response.text,
                    )
                except Exception:
                    pass
            try:
                return response.json()
            except Exception:
                return response.text
        # Error handling
        if _HTTP_DEBUG:
            try:
                logger.error(
                    "HTTP DEBUG error: %s %s -> %s\nHeaders: %s\nBody: %s",
                    getattr(response.request, "method", ""),
                    getattr(response.request, "url", ""),
                    response.status_code,
                    dict(response.headers),
                    response.text,
                )
            except Exception:
                pass
        # Parse error payload safely to build error details
        try:
            parsed = response.json()
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            error_data = parsed
            error_message = parsed.get("error") or parsed.get("message") or f"HTTP {response.status_code}"
        else:
            error_data = {"raw": response.text}
            error_message = f"HTTP {response.status_code}"
        logger.error(f"API Error {response.status_code}: {error_message}")
        
        if response.status_code == 401:
            logger.error("Authentication error - invalid credentials or API key")
            raise AuthenticationError(error_message)
        elif response.status_code == 403:
            logger.error("Authorization error - insufficient permissions")
            raise AuthorizationError(error_message)
        elif response.status_code == 404:
            logger.error(f"Resource not found: {response.request.url}")
            raise NotFoundError(error_message)
        elif response.status_code == 429:
            logger.error("Rate limit exceeded")
            raise RateLimitError(error_message)
        else:
            logger.error(f"API error: {error_message}, details: {error_data}")
            raise YouGileError(error_message, status_code=response.status_code, details=error_data)
    
    # Convenience methods
    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make GET request."""
        return await self.request("GET", path, params=params)
    
    async def post(self, path: str, json: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make POST request.""" 
        return await self.request("POST", path, json=json, params=params)
    
    async def put(self, path: str, json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Make PUT request."""
        return await self.request("PUT", path, json=json)
    
    async def delete(self, path: str) -> Dict[str, Any]:
        """Make DELETE request."""
        return await self.request("DELETE", path)