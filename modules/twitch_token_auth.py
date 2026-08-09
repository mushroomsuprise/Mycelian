# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared Twitch OAuth token validation, refresh, and expiry helpers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, List, Optional

import aiohttp
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope, AuthType, InvalidRefreshTokenException

logger = logging.getLogger(__name__)

# Twitch user access tokens last ~4 hours. Conservative fallback when validate is unreachable.
ACCESS_TOKEN_FALLBACK_LIFETIME = timedelta(hours=3, minutes=30)
# Proactive refresh fires this many minutes before the access token actually expires.
TOKEN_PROACTIVE_REFRESH_BUFFER = timedelta(minutes=5)
# Stored expiry farther than this likely came from the old 60-day bug.
LEGACY_EXPIRY_MIGRATION_THRESHOLD = timedelta(hours=24)

TWITCH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"


@dataclass
class ValidateResult:
    ok: bool
    outcome: str
    client_id: Optional[str] = None
    expires_in: Optional[int] = None
    http_status: Optional[int] = None
    message: Optional[str] = None


@dataclass
class RefreshResult:
    success: bool
    outcome: str
    auth_token: str = ""
    refresh_token: str = ""
    token_expiry: Optional[datetime] = None


async def validate_access_token(
    token: str,
    expected_client_id: Optional[str] = None,
) -> ValidateResult:
    """Validate a user access token via Twitch's validate endpoint."""
    token = (token or "").strip()
    if not token:
        return ValidateResult(ok=False, outcome="missing_token", message="empty token")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TWITCH_VALIDATE_URL,
                headers={"Authorization": f"OAuth {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    token_client_id = str(payload.get("client_id", "") or "")
                    if expected_client_id and token_client_id != expected_client_id:
                        return ValidateResult(
                            ok=False,
                            outcome="client_id_mismatch",
                            client_id=token_client_id,
                            expires_in=int(payload.get("expires_in", 0) or 0) or None,
                            http_status=200,
                            message=(
                                "Token was issued for a different Twitch client id; "
                                "re-authenticate after updating Settings credentials"
                            ),
                        )
                    return ValidateResult(
                        ok=True,
                        outcome="validated",
                        client_id=token_client_id,
                        expires_in=int(payload.get("expires_in", 0) or 0) or None,
                        http_status=200,
                    )
                if resp.status == 401:
                    return ValidateResult(
                        ok=False,
                        outcome="invalid_token",
                        http_status=401,
                        message="access token invalid or expired",
                    )
                return ValidateResult(
                    ok=False,
                    outcome="transient_error",
                    http_status=resp.status,
                    message=f"validate returned HTTP {resp.status}",
                )
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        return ValidateResult(
            ok=False,
            outcome="transient_error",
            message=str(e),
        )
    except Exception as e:
        logger.warning("Unexpected error validating Twitch token: %s", e)
        return ValidateResult(
            ok=False,
            outcome="transient_error",
            message=str(e),
        )


async def compute_token_expiry(token: str) -> datetime:
    """Resolve real expiry for a Twitch user access token."""
    result = await validate_access_token(token)
    if result.ok and result.expires_in is not None:
        if result.expires_in > 0:
            return datetime.now() + timedelta(seconds=result.expires_in)
        return datetime.now() + timedelta(days=60)
    if result.outcome == "transient_error":
        logger.warning(
            "Could not validate Twitch token expiry (%s); using fallback lifetime",
            result.message,
        )
    return datetime.now() + ACCESS_TOKEN_FALLBACK_LIFETIME


def is_access_token_expired(
    auth_token: str,
    token_expiry: Optional[datetime],
    *,
    buffer: timedelta = TOKEN_PROACTIVE_REFRESH_BUFFER,
) -> bool:
    """Return True when the access token should be refreshed proactively."""
    if not (auth_token or "").strip():
        return True
    if token_expiry is None:
        return False
    return datetime.now() + buffer >= token_expiry


def legacy_expiry_needs_migration(token_expiry: Optional[datetime]) -> bool:
    """True when stored expiry is probably the old 60-day placeholder."""
    if token_expiry is None:
        return True
    return token_expiry - datetime.now() > LEGACY_EXPIRY_MIGRATION_THRESHOLD


async def is_token_currently_valid(
    auth_token: str,
    expected_client_id: Optional[str] = None,
) -> bool:
    """Check validate endpoint — used by refresh dedup short-circuits."""
    result = await validate_access_token(auth_token, expected_client_id)
    return result.ok


def twitch_has_user_auth(twitch: Optional[Twitch]) -> bool:
    """True when the twitchAPI client has user authentication."""
    if twitch is None:
        return False
    try:
        return twitch.has_required_auth(AuthType.USER, [])
    except Exception:
        return False


async def create_twitch_client(client_id: str, client_secret: str) -> Twitch:
    """Create a twitchAPI client with app authentication bootstrapped."""
    twitch = Twitch(client_id, client_secret)
    await twitch
    return twitch


def attach_refresh_callback(
    twitch: Twitch,
    on_tokens_refreshed: Callable[[str, str], Awaitable[None]],
) -> None:
    """Persist tokens when twitchAPI auto-refreshes on 401."""

    async def _callback(auth_token: str, refresh_token: str) -> None:
        try:
            await on_tokens_refreshed(auth_token, refresh_token)
        except Exception as e:
            logger.error("Error in Twitch token refresh callback: %s", e, exc_info=True)

    twitch.user_auth_refresh_callback = _callback


def is_definitive_refresh_failure(exc: BaseException) -> bool:
    """True when the refresh token is invalid/revoked (not a transient error)."""
    if isinstance(exc, InvalidRefreshTokenException):
        return True
    msg = str(exc).lower()
    if "invalid refresh" in msg or "revoked" in msg:
        return True
    if "invalid refresh token" in msg:
        return True
    return False


def is_credential_config_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "client secret" in msg or "client_id" in msg


async def apply_user_authentication(
    twitch: Twitch,
    auth_token: str,
    refresh_token: str,
    scopes: List[AuthScope],
) -> None:
    """Set user auth on a Twitch client."""
    await twitch.set_user_authentication(auth_token, scopes, refresh_token)


async def refresh_user_token(
    twitch: Twitch,
    *,
    client_id: str,
    client_secret: str,
    auth_token: str,
    refresh_token: str,
    scopes: List[AuthScope],
) -> RefreshResult:
    """Refresh user tokens and confirm success via the validate endpoint."""
    auth_token = (auth_token or "").strip()
    refresh_token = (refresh_token or "").strip()
    if not refresh_token:
        return RefreshResult(success=False, outcome="missing_refresh_token")

    if twitch.app_id != client_id:
        twitch.app_id = client_id
    if twitch.app_secret != client_secret:
        twitch.app_secret = client_secret

    await apply_user_authentication(twitch, auth_token, refresh_token, scopes)

    try:
        await twitch.refresh_used_token()
    except InvalidRefreshTokenException as e:
        logger.warning("Twitch refresh token invalid: %s", e)
        return RefreshResult(success=False, outcome="invalid_refresh_token")
    except Exception as e:
        if is_credential_config_error(e):
            logger.warning(
                "Cannot refresh Twitch token (check client id/secret in Settings): %s",
                e,
            )
            return RefreshResult(success=False, outcome="credential_error")
        if is_definitive_refresh_failure(e):
            logger.warning("Twitch refresh failed definitively: %s", e)
            return RefreshResult(success=False, outcome="invalid_refresh_token")
        logger.warning("Transient error refreshing Twitch token: %s", e)
        return RefreshResult(success=False, outcome="transient_error")

    new_auth_token = getattr(twitch, "_user_auth_token", None) or ""
    new_refresh_token = getattr(twitch, "_user_auth_refresh_token", None) or ""

    if not new_auth_token or not new_refresh_token:
        # Tokens may be unchanged but still valid — validate what we have.
        candidate = auth_token
        validation = await validate_access_token(candidate, client_id)
        if validation.ok:
            expiry = await compute_token_expiry(candidate)
            logger.info(
                "Twitch token refresh: unchanged_but_valid (outcome=%s)",
                validation.outcome,
            )
            return RefreshResult(
                success=True,
                outcome="unchanged_but_valid",
                auth_token=candidate,
                refresh_token=refresh_token,
                token_expiry=expiry,
            )
        return RefreshResult(success=False, outcome="empty_tokens")

    validation = await validate_access_token(new_auth_token, client_id)
    if not validation.ok:
        if validation.outcome == "client_id_mismatch":
            logger.warning("Twitch token refresh: %s", validation.message)
            return RefreshResult(success=False, outcome="client_id_mismatch")
        if validation.outcome == "invalid_token":
            return RefreshResult(success=False, outcome="invalid_refresh_token")
        return RefreshResult(success=False, outcome=validation.outcome)

    # Re-apply auth so twitch instance scopes stay in sync after refresh.
    await apply_user_authentication(
        twitch, new_auth_token, new_refresh_token, scopes
    )

    token_expiry = await compute_token_expiry(new_auth_token)
    outcome = (
        "validated"
        if new_auth_token != auth_token or new_refresh_token != refresh_token
        else "unchanged_but_valid"
    )
    logger.info("Twitch token refresh succeeded (outcome=%s)", outcome)
    return RefreshResult(
        success=True,
        outcome=outcome,
        auth_token=new_auth_token,
        refresh_token=new_refresh_token,
        token_expiry=token_expiry,
    )
