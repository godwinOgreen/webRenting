"""
Comprehensive sandbox test for all core infrastructure.

Tests every module built so far without requiring a running server.
Uses real Redis (Memurai) and real PostgreSQL where possible.

Usage:
    cd C:\\Users\\godwi\\Documents\\webRenting
    python tests/test_core_sandbox.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import selectors
import sys

# Windows: psycopg 3 requires SelectorEventLoop, not ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.permissions.guards import (
    require_role,
    #require_admin_level,
    require_kyc,
    #equire_verified,
    )

_pass = 0
_fail = 0
_skip = 0


def ok(label: str):
    global _pass
    _pass += 1
    print(f"  PASS  {label}")


def fail(label: str, detail: str = ""):
    global _fail
    _fail += 1
    print(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def skip(label: str, reason: str = ""):
    global _skip
    _skip += 1
    print(f"  SKIP  {label}" + (f" -- {reason}" if reason else ""))


def section(title: str):
    print(f"\n{'=' * 56}")
    print(f"  {title}")
    print(f"{'=' * 56}")


# =====================================================================
#  1. CONFIG
# =====================================================================

def test_config():
    section("1. CONFIG (core/config.py)")

    try:
        from app.core.config import settings
        ok("Settings imported")
    except Exception as e:
        fail("Settings import", str(e))
        return

    checks = {
        "APP_NAME": settings.APP_NAME,
        "DATABASE_URL": settings.DATABASE_URL,
        "SECRET_KEY": settings.SECRET_KEY,
        "JWT_ALGORITHM": settings.JWT_ALGORITHM,
        "ACCESS_TOKEN_EXPIRE_MINUTES": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "REFRESH_TOKEN_EXPIRE_DAYS": settings.REFRESH_TOKEN_EXPIRE_DAYS,
        "REDIS_URL": settings.REDIS_URL,
        "API_V1_PREFIX": settings.API_V1_PREFIX,
    }

    for name, value in checks.items():
        if value is not None and value != "":
            ok(f"settings.{name} = {repr(value)[:40]}")
        else:
            fail(f"settings.{name}", "is None or empty")

    if settings.JWT_ALGORITHM == "HS256":
        ok("JWT_ALGORITHM is HS256")
    else:
        fail("JWT_ALGORITHM", f"expected HS256, got {settings.JWT_ALGORITHM}")

    if isinstance(settings.BACKEND_CORS_ORIGINS, list):
        ok(f"BACKEND_CORS_ORIGINS = {settings.BACKEND_CORS_ORIGINS}")
    else:
        fail("BACKEND_CORS_ORIGINS", f"expected list, got {type(settings.BACKEND_CORS_ORIGINS)}")


# =====================================================================
#  2. EXCEPTIONS
# =====================================================================

def test_exceptions():
    section("2. EXCEPTIONS (core/exceptions.py)")

    from app.core.exceptions import (
        BaseAppException,
        ValidationException,
        UnauthorizedException,
        PaymentException,
        ForbiddenException,
        KYCException,
        NotFoundException,
        ConflictException,
        RateLimitException,
    )

    status_map = {
        ValidationException: 400,
        UnauthorizedException: 401,
        PaymentException: 402,
        ForbiddenException: 403,
        KYCException: 403,
        NotFoundException: 404,
        ConflictException: 409,
        RateLimitException: 429,
        BaseAppException: 500,
    }

    for exc_cls, expected_code in status_map.items():
        exc = exc_cls()
        if exc.status_code == expected_code:
            ok(f"{exc_cls.__name__}.status_code = {expected_code}")
        else:
            fail(f"{exc_cls.__name__}.status_code",
                 f"expected {expected_code}, got {exc.status_code}")

    exc = ValidationException(
        message="bad data",
        errors={"price": ["must be positive"]},
        error_code="validation_error",
    )
    d = exc.to_dict()
    if all(k in d for k in ("success", "message", "errors", "error_code")):
        ok(f"to_dict() has all keys: {list(d.keys())}")
    else:
        fail("to_dict()", f"missing keys: {d}")

    if d["success"] is False:
        ok("to_dict()['success'] = False")
    else:
        fail("to_dict()['success']", f"expected False, got {d['success']}")

    if issubclass(KYCException, ForbiddenException):
        ok("KYCException IS-A ForbiddenException")
    else:
        fail("KYCException", "not a subclass of ForbiddenException")

    exc = ForbiddenException(
        message="Not allowed",
        error_code="not_resource_owner",
        log_context={"user_id": "abc"},
    )
    if exc.error_code == "not_resource_owner" and exc.log_context == {"user_id": "abc"}:
        ok("Custom error_code and log_context work")
    else:
        fail("Custom error_code/log_context", str(exc.to_dict()))


# =====================================================================
#  3. SECURITY
# =====================================================================

def test_security():
    section("3. SECURITY (core/security.py)")

    from app.core.security import (
        hash_password,
        verify_password,
        create_access_token,
        create_refresh_token,
        decode_token,
        get_subject_from_token,
        TokenType,
        TokenExpiredError,
        InvalidTokenError,
        TokenError,
    )

    # Password hashing
    pw = "MySecureP@ssw0rd!"
    hashed = hash_password(pw)

    if hashed != pw and len(hashed) > 50:
        ok("hash_password() produces bcrypt hash")
    else:
        fail("hash_password()", "output doesn't look like a bcrypt hash")

    if verify_password(pw, hashed):
        ok("verify_password() correct password -> True")
    else:
        fail("verify_password()", "correct password returned False")

    if not verify_password("wrong_password", hashed):
        ok("verify_password() wrong password -> False")
    else:
        fail("verify_password()", "wrong password returned True")

    if not verify_password(pw, "$2b$12$invalidhash"):
        ok("verify_password() malformed hash -> False (no crash)")
    else:
        fail("verify_password()", "malformed hash should return False")

    # 72-byte limit
    try:
        hash_password("a" * 100)
        fail("hash_password()", "should reject > 72 bytes")
    except ValueError:
        ok("hash_password() rejects passwords > 72 bytes")

    # Access token
    user_id = uuid.uuid4()
    access = create_access_token(user_id)
    decoded = decode_token(access, TokenType.ACCESS)

    if decoded["sub"] == str(user_id):
        ok("create_access_token -> decode_token: sub matches user_id")
    else:
        fail("access token sub", f"expected {user_id}, got {decoded['sub']}")

    if decoded["type"] == "access":
        ok("access token type = 'access'")
    else:
        fail("access token type", f"expected 'access', got {decoded['type']}")

    if "jti" in decoded:
        ok(f"access token has jti claim: {decoded['jti'][:12]}...")
    else:
        fail("access token", "missing jti claim")

    if "exp" in decoded and "iat" in decoded:
        ok("access token has exp and iat claims")
    else:
        fail("access token", "missing exp or iat")

    # Refresh token
    refresh = create_refresh_token(user_id)
    decoded_r = decode_token(refresh, TokenType.REFRESH)

    if decoded_r["type"] == "refresh":
        ok("refresh token type = 'refresh'")
    else:
        fail("refresh token type", f"expected 'refresh', got {decoded_r['type']}")

    # Token type enforcement
    try:
        decode_token(access, TokenType.REFRESH)
        fail("type enforcement", "access token accepted as refresh")
    except InvalidTokenError:
        ok("access token rejected when expecting refresh")

    try:
        decode_token(refresh, TokenType.ACCESS)
        fail("type enforcement", "refresh token accepted as access")
    except InvalidTokenError:
        ok("refresh token rejected when expecting access")

    # get_subject_from_token
    subject = get_subject_from_token(access, TokenType.ACCESS)
    if subject == str(user_id):
        ok("get_subject_from_token returns correct UUID string")
    else:
        fail("get_subject_from_token", f"expected {user_id}, got {subject}")

    # Invalid token
    try:
        decode_token("not.a.valid.token", TokenType.ACCESS)
        fail("invalid token", "should have raised")
    except InvalidTokenError:
        ok("Invalid token raises InvalidTokenError")

    # Extra claims
    token_with_claims = create_access_token(
        user_id,
        extra_claims={"device": "mobile"},
    )
    decoded_extra = decode_token(token_with_claims, TokenType.ACCESS)
    if decoded_extra.get("device") == "mobile":
        ok("extra_claims passed through to token")
    else:
        fail("extra_claims", f"got {decoded_extra.get('device')}")

    # Exception hierarchy
    if issubclass(TokenExpiredError, TokenError):
        ok("TokenExpiredError IS-A TokenError")
    else:
        fail("TokenExpiredError", "not a subclass of TokenError")

    if issubclass(InvalidTokenError, TokenError):
        ok("InvalidTokenError IS-A TokenError")
    else:
        fail("InvalidTokenError", "not a subclass of TokenError")


# =====================================================================
#  4. DATABASE SESSION
# =====================================================================

def test_database():
    section("4. DATABASE (app/db/session.py)")

    try:
        from app.db.session import (
            engine,
            async_session_factory,
            get_db,
            DATABASE_URL,
        )
        ok("Session imports OK")
    except Exception as e:
        fail("Session imports", str(e))
        return

    if "+asyncpg" in DATABASE_URL:
        fail("DATABASE_URL", "still using asyncpg driver")
    elif "+psycopg" in DATABASE_URL:
        ok("DATABASE_URL uses psycopg driver")
    else:
        ok(f"DATABASE_URL: {DATABASE_URL[:50]}...")

    if engine is not None:
        ok(f"Engine created: {engine.name}")
    else:
        fail("Engine", "is None")


async def test_database_connectivity():
    section("5. DATABASE CONNECTIVITY")

    try:
        from app.db.session import engine, async_session_factory
        from sqlalchemy import text

        async with async_session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            val = result.scalar()
            if val == 1:
                ok("SELECT 1 -> database is reachable")
            else:
                fail("SELECT 1", f"unexpected result: {val}")

            result = await session.execute(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            ))
            count = result.scalar()
            if count >= 27:
                ok(f"Table count: {count} (expected >= 27)")
            else:
                fail("Table count", f"expected >= 27, got {count}")

            result = await session.execute(text(
                "SELECT count(*) FROM pg_type WHERE typtype = 'e'"
            ))
            enum_count = result.scalar()
            if enum_count >= 21:
                ok(f"Enum types: {enum_count} (expected >= 21)")
            else:
                fail("Enum types", f"expected >= 21, got {enum_count}")

            result = await session.execute(text(
                "SELECT count(*) FROM information_schema.triggers "
                "WHERE trigger_name = 'set_updated_at'"
            ))
            trigger_count = result.scalar()
            if trigger_count >= 10:
                ok(f"updated_at triggers: {trigger_count} (expected >= 10)")
            else:
                fail("updated_at triggers", f"expected >= 10, got {trigger_count}")

            result = await session.execute(text(
                "SELECT extname FROM pg_extension WHERE extname = 'postgis'"
            ))
            ext = result.scalar_one_or_none()
            if ext == "postgis":
                ok("PostGIS extension active")
            else:
                fail("PostGIS", f"expected 'postgis', got {ext}")

    except Exception as e:
        fail("Database connectivity", str(e))


# =====================================================================
#  6. REDIS
# =====================================================================

async def test_redis():
    section("6. REDIS (core/redis_client.py)")

    try:
        from app.core.redis_client import (
            redis_client,
            RedisKeys,
            connect_redis,
            disconnect_redis,
        )
        ok("Redis client imports OK")
    except Exception as e:
        fail("Redis imports", str(e))
        return

    try:
        await connect_redis()
        ok(f"Redis connected")
    except Exception as e:
        fail("Redis connect", str(e))
        return

    # Basic operations
    test_key = "sandbox:test:ping"
    await redis_client.set(test_key, "pong", ex=60)
    val = await redis_client.get(test_key)
    if val == "pong":
        ok("SET/GET round-trip works")
    else:
        fail("SET/GET", f"expected 'pong', got {val!r}")

    exists = await redis_client.exists(test_key)
    if exists == 1:
        ok("EXISTS returns 1 for existing key")
    else:
        fail("EXISTS", f"expected 1, got {exists}")

    await redis_client.delete(test_key)
    val = await redis_client.get(test_key)
    if val is None:
        ok("DELETE removes key")
    else:
        fail("DELETE", f"key still has value: {val!r}")

    # Key namespace
    sub_key = RedisKeys.active_subscription("test-user-123")
    rl_key = RedisKeys.rate_limit("login", "192.168.1.1")
    kyc_key = RedisKeys.kyc_status("test-user-123")
    rt_key = RedisKeys.refresh_token_blacklist("test-jti-456")

    if sub_key == "sub:active:test-user-123":
        ok(f"RedisKeys.active_subscription -> {sub_key}")
    else:
        fail("RedisKeys.active_subscription", sub_key)

    if rl_key == "rl:login:192.168.1.1":
        ok(f"RedisKeys.rate_limit -> {rl_key}")
    else:
        fail("RedisKeys.rate_limit", rl_key)

    if kyc_key == "kyc:status:test-user-123":
        ok(f"RedisKeys.kyc_status -> {kyc_key}")
    else:
        fail("RedisKeys.kyc_status", kyc_key)

    if rt_key == "rt:blacklist:test-jti-456":
        ok(f"RedisKeys.refresh_token_blacklist -> {rt_key}")
    else:
        fail("RedisKeys.refresh_token_blacklist", rt_key)

    # TTL constants
    if RedisKeys.SUBSCRIPTION_TTL_SECONDS == 300:
        ok(f"SUBSCRIPTION_TTL_SECONDS = {RedisKeys.SUBSCRIPTION_TTL_SECONDS}")
    else:
        fail("SUBSCRIPTION_TTL_SECONDS", str(RedisKeys.SUBSCRIPTION_TTL_SECONDS))

    if RedisKeys.RATE_LIMIT_TTL_SECONDS == 60:
        ok(f"RATE_LIMIT_TTL_SECONDS = {RedisKeys.RATE_LIMIT_TTL_SECONDS}")
    else:
        fail("RATE_LIMIT_TTL_SECONDS", str(RedisKeys.RATE_LIMIT_TTL_SECONDS))

    # Rate limiting with sorted set
    rate_key = "sandbox:test:ratelimit"
    await redis_client.delete(rate_key)
    allowed_count = 0
    blocked_count = 0

    for i in range(8):
        pipe = redis_client.pipeline()
        pipe.zcard(rate_key)                        # count BEFORE adding
        pipe.zadd(rate_key, {str(i): float(i)})     # add this request
        pipe.expire(rate_key, 60)                    # auto-cleanup
        results = await pipe.execute()               # single await
        count = results[0]                           # zcard result
        if count < 5:
            allowed_count += 1
        else:
            blocked_count += 1

    await redis_client.delete(rate_key)

    if allowed_count == 5 and blocked_count == 3:
        ok(f"Rate limiting: {allowed_count} allowed, {blocked_count} blocked (limit=5)")
    else:
        fail("Rate limiting", f"{allowed_count} allowed, {blocked_count} blocked")
    # Cleanup
    await redis_client.delete(sub_key)
    await redis_client.delete(rt_key)


# =====================================================================
#  7. PERMISSIONS / ROLES
# =====================================================================

def test_roles():
    section("7. PERMISSIONS / ROLES (permissions/roles.py)")

    from app.domains.users.models import AdminRole, UserRole
    from app.permissions.roles import (
        _ADMIN_HIERARCHY,
        is_renter, is_agent, is_admin,
        is_moderator, is_standard_admin, is_super_admin,
        can_moderate, can_manage_users, can_manage_platform,
        display_role,
    )

    ok("roles.py imports OK")

    users = {
        "renter": SimpleNamespace(
            role=UserRole.RENTER, admin_role=None,
            first_name="R", last_name="User", email="r@test.com",
        ),
        "agent": SimpleNamespace(
            role=UserRole.AGENT, admin_role=None,
            first_name="A", last_name="User", email="a@test.com",
        ),
        "admin": SimpleNamespace(
            role=UserRole.ADMIN, admin_role=AdminRole.ADMIN,
            first_name="Ad", last_name="User", email="ad@test.com",
        ),
        "moderator": SimpleNamespace(
            role=UserRole.ADMIN, admin_role=AdminRole.MODERATOR,
            first_name="Mod", last_name="User", email="mod@test.com",
        ),
        "super_admin": SimpleNamespace(
            role=UserRole.ADMIN, admin_role=AdminRole.SUPER_ADMIN,
            first_name="SA", last_name="User", email="sa@test.com",
        ),
    }

    tests = [
        (is_renter, "renter", True), (is_renter, "agent", False), (is_renter, "admin", False),
        (is_agent, "renter", False), (is_agent, "agent", True), (is_agent, "admin", False),
        (is_admin, "renter", False), (is_admin, "admin", True), (is_admin, "moderator", True),
        (is_admin, "super_admin", True),
        (is_moderator, "admin", False), (is_moderator, "moderator", True),
        (is_moderator, "super_admin", False),
        (is_standard_admin, "admin", True), (is_standard_admin, "moderator", False),
        (is_standard_admin, "super_admin", False),
        (is_super_admin, "admin", False), (is_super_admin, "moderator", False),
        (is_super_admin, "super_admin", True),
        (can_moderate, "renter", False), (can_moderate, "agent", False),
        (can_moderate, "admin", True), (can_moderate, "moderator", True),
        (can_moderate, "super_admin", True),
        (can_manage_users, "renter", False), (can_manage_users, "moderator", False),
        (can_manage_users, "admin", True), (can_manage_users, "super_admin", True),
        (can_manage_platform, "renter", False), (can_manage_platform, "moderator", False),
        (can_manage_platform, "admin", False), (can_manage_platform, "super_admin", True),
    ]

    for pred, user_key, expected in tests:
        result = pred(users[user_key])
        if result == expected:
            ok(f"{pred.__name__}({user_key}) = {result}")
        else:
            fail(f"{pred.__name__}({user_key})", f"expected {expected}, got {result}")

    display_tests = [
        ("renter", "Renter"),
        ("agent", "Agent"),
        ("admin", "Admin"),
        ("moderator", "Moderator"),
        ("super_admin", "Super Admin"),
    ]
    for user_key, expected in display_tests:
        result = display_role(users[user_key])
        if result == expected:
            ok(f"display_role({user_key}) = {result!r}")
        else:
            fail(f"display_role({user_key})", f"expected {expected!r}, got {result!r}")

    if _ADMIN_HIERARCHY == {"moderator": 0, "admin": 1, "super_admin": 2}:
        ok(f"_ADMIN_HIERARCHY = {_ADMIN_HIERARCHY}")
    else:
        fail("_ADMIN_HIERARCHY", str(_ADMIN_HIERARCHY))


# =====================================================================
#  8. PERMISSIONS / GUARDS
# =====================================================================

async def test_guards():
    section("8. PERMISSIONS / GUARDS (permissions/guards.py)")

    from app.domains.users.models import AdminRole, KycStatus, UserRole
    from app.core.exceptions import ForbiddenException, KYCException
    from app.permissions.guards import (
        require_role,
        require_kyc,
        require_moderator,
        require_super_admin,
        require_agent,
    )

    ok("guards.py imports OK")

    # -- Helper to simulate FastAPI Depends resolution --
    async def call_guard(guard_factory, user):
        guard = guard_factory()
        return await guard(current_user=user)

    # -- Mock users --
    renter = SimpleNamespace(
        id=uuid.uuid4(), role=UserRole.RENTER, admin_role=None,
        kyc_status=KycStatus.VERIFIED, verified=True, suspended_at=None,
    )
    agent = SimpleNamespace(
        id=uuid.uuid4(), role=UserRole.AGENT, admin_role=None,
        kyc_status=KycStatus.VERIFIED, verified=True, suspended_at=None,
    )
    admin = SimpleNamespace(
        id=uuid.uuid4(), role=UserRole.ADMIN, admin_role=AdminRole.ADMIN,
        kyc_status=KycStatus.VERIFIED, verified=True, suspended_at=None,
    )
    mod = SimpleNamespace(
        id=uuid.uuid4(), role=UserRole.ADMIN, admin_role=AdminRole.MODERATOR,
        kyc_status=KycStatus.VERIFIED, verified=True, suspended_at=None,
    )
    super_admin = SimpleNamespace(
        id=uuid.uuid4(), role=UserRole.ADMIN, admin_role=AdminRole.SUPER_ADMIN,
        kyc_status=KycStatus.VERIFIED, verified=True, suspended_at=None,
    )

    # -- require_role: should pass --
    try:
        await call_guard(require_role(UserRole.RENTER), renter)
        ok("require_role(RENTER)(renter) -> pass")
    except Exception as e:
        fail("require_role(RENTER)(renter)", str(e))

    try:
        await call_guard(require_role(UserRole.AGENT), agent)
        ok("require_role(AGENT)(agent) -> pass")
    except Exception as e:
        fail("require_role(AGENT)(agent)", str(e))

    try:
        await call_guard(require_role(UserRole.ADMIN), admin)
        ok("require_role(ADMIN)(admin) -> pass")
    except Exception as e:
        fail("require_role(ADMIN)(admin)", str(e))

    # -- require_role: should fail --
    try:
        await call_guard(require_role(UserRole.AGENT), renter)
        fail("require_role(AGENT)(renter)", "should have raised")
    except ForbiddenException as e:
        if e.error_code == "insufficient_role":
            ok("require_role(AGENT)(renter) -> ForbiddenException(insufficient_role)")
        else:
            fail("error_code", f"got {e.error_code}")

    try:
        await call_guard(require_role(UserRole.ADMIN), agent)
        fail("require_role(ADMIN)(agent)", "should have raised")
    except ForbiddenException as e:
        if e.error_code == "insufficient_role":
            ok("require_role(ADMIN)(agent) -> ForbiddenException(insufficient_role)")
        else:
            fail("error_code", f"got {e.error_code}")

    # -- require_moderator: should pass for admin, mod, super_admin --
    for u, name in [(admin, "admin"), (mod, "moderator"), (super_admin, "super_admin")]:
        try:
            await call_guard(require_moderator, u)
            ok(f"require_moderator({name}) -> pass")
        except Exception as e:
            fail(f"require_moderator({name})", str(e))

    # -- require_moderator: should fail for renter, agent --
    for u, name in [(renter, "renter"), (agent, "agent")]:
        try:
            await call_guard(require_moderator, u)
            fail(f"require_moderator({name})", "should have raised")
        except ForbiddenException as e:
            if e.error_code == "insufficient_role":
                ok(f"require_moderator({name}) -> ForbiddenException(insufficient_role)")
            else:
                fail("error_code", f"got {e.error_code}")

    # -- require_super_admin: should pass for super_admin only --
    try:
        await call_guard(require_super_admin, super_admin)
        ok("require_super_admin(super_admin) -> pass")
    except Exception as e:
        fail("require_super_admin(super_admin)", str(e))

    for u, name in [(admin, "admin"), (mod, "moderator"), (renter, "renter")]:
        try:
            await call_guard(require_super_admin, u)
            fail(f"require_super_admin({name})", "should have raised")
        except ForbiddenException as e:
            if e.error_code == "insufficient_role":
                ok(f"require_super_admin({name}) -> ForbiddenException(insufficient_role)")
            else:
                fail("error_code", f"got {e.error_code}")

    # -- require_kyc (Depends callable pattern) --
    kyc_verified = SimpleNamespace(
        id=uuid.uuid4(), kyc_status=KycStatus.VERIFIED,
    )
    kyc_none = SimpleNamespace(
        id=uuid.uuid4(), kyc_status=KycStatus.NOT_SUBMITTED,
    )
    kyc_pending = SimpleNamespace(
        id=uuid.uuid4(), kyc_status=KycStatus.PENDING_REVIEW,
    )
    kyc_rejected = SimpleNamespace(
        id=uuid.uuid4(), kyc_status=KycStatus.REJECTED,
    )

    try:
        await call_guard(require_kyc, kyc_verified)
        ok("require_kyc(verified) -> pass")
    except Exception as e:
        fail("require_kyc(verified)", str(e))

    kyc_states = [
        (kyc_none, "kyc_required"),
        (kyc_pending, "kyc_pending"),
        (kyc_rejected, "kyc_rejected"),
    ]
    for user, expected_code in kyc_states:
        try:
            await call_guard(require_kyc, user)
            fail(f"require_kyc({expected_code})", "should have raised")
        except KYCException as e:
            if e.error_code == expected_code:
                ok(f"require_kyc({user.kyc_status.value}) -> KYCException({expected_code})")
            else:
                fail("require_kyc error_code",
                     f"expected {expected_code}, got {e.error_code}")

    # -- require_agent (composite guard) --
    try:
        guard = require_agent()
        ok("require_agent() factory returns callable")
    except Exception as e:
        fail("require_agent() factory", str(e))


# =====================================================================
#  9. DEPENDENCIES
# =====================================================================

def test_dependencies():
    section("9. DEPENDENCIES (core/dependencies.py) -- import check")

    try:
        from app.core.dependencies import (
            get_current_user,
            get_optional_user,
            _bearer_scheme,
        )
        ok("dependencies.py imports OK")
        ok(f"get_current_user: {get_current_user.__name__}")
        ok(f"get_optional_user: {get_optional_user.__name__}")
    except Exception as e:
        fail("dependencies.py imports", str(e))


# =====================================================================
#  10. MAIN APP
# =====================================================================

async def test_main_app():
    section("10. MAIN APP (main.py)")

    try:
        from httpx import AsyncClient, ASGITransport
    except ImportError:
        skip("main.py tests", "httpx not installed -- pip install httpx")
        return

    try:
        from app.main import app
        ok("app imported from main.py")
    except Exception as e:
        fail("app import", str(e))
        return

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        # Health check
        try:
            r = await client.get("/health")
            if r.status_code == 200 and r.json().get("status") == "ok":
                ok(f"GET /health -> {r.json()}")
            else:
                fail("GET /health", f"status={r.status_code}, body={r.text}")
        except Exception as e:
            fail("GET /health", str(e))

        # Swagger docs
        try:
            r = await client.get("/docs")
            if r.status_code == 200:
                ok("GET /docs -> 200 (Swagger UI available)")
            else:
                fail("GET /docs", f"status={r.status_code}")
        except Exception as e:
            fail("GET /docs", str(e))

        # 404
        try:
            r = await client.get("/nonexistent")
            if r.status_code == 404:
                body = r.json()
                if body.get("success") is False and body.get("error_code") == "not_found":
                    ok("GET /nonexistent -> 404 with standard error shape")
                else:
                    fail("404 shape", str(body))
            else:
                fail("GET /nonexistent", f"expected 404, got {r.status_code}")
        except Exception as e:
            fail("GET /nonexistent", str(e))

        # CORS
        try:
            r = await client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
            if "access-control-allow-origin" in r.headers:
                ok(f"CORS: allow-origin = {r.headers['access-control-allow-origin']}")
            else:
                skip("CORS headers", "may need different test approach")
        except Exception as e:
            skip("CORS test", str(e))


# =====================================================================
#  RUNNER
# =====================================================================

async def main():
    print("=" * 56)
    print("  COMPREHENSIVE CORE INFRASTRUCTURE TEST")
    print("=" * 56)

    # Sync tests
    test_config()
    test_exceptions()
    test_security()
    test_roles()
    test_guards()
    test_dependencies()

    # Async tests
    test_database()
    await test_database_connectivity()
    await test_redis()
    await test_main_app()

    # Summary
    total = _pass + _fail + _skip
    print(f"\n{'=' * 56}")
    print(f"  RESULTS: {_pass} passed, {_fail} failed, {_skip} skipped")
    print(f"  TOTAL:   {total} tests")
    if _fail == 0:
        print("  ALL TESTS PASSED")
    else:
        print(f"  {_fail} FAILURES -- check output above")
    print("=" * 56)

    sys.exit(1 if _fail > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())