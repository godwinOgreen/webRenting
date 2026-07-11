# IMPORT RULES & ARCHITECTURE STANDARDS v1.0

## 1. Layer Ownership

| Layer | Owns | Never Touches |
| --- | --- | --- |
| router.py | HTTP request/response, calling service | db.query, db.add, db.flush, db.commit, models, business logic |
| service.py | Business rules, validation, orchestration | db.query, db.add, db.flush, db.commit, db.execute, raw SQL |
| repository.py | ALL persistence: queries, writes, flush | db.commit, db.rollback, business rules, HTTP exceptions |
| dependencies.py (get_db) | db.commit, db.rollback, session lifecycle | Business logic |

## 2. The Flow

    Router → Service → Repository → Database

    db.commit()    happens ONLY in get_db() dependency
    db.flush()     happens ONLY in repository
    db.query()     happens ONLY in repository (via SQLAlchemy select())

## 3. The One Rule (Cross-Domain Imports)

    ✅ Service can import models from any domain
    ✅ Service can import schemas from any domain
    ❌ Service CANNOT import services from other domains
    ❌ Service CANNOT import repositories from other domains
    ✅ Repository can import models from any domain
    ❌ Repository CANNOT import repositories from other domains

## 4. Import Permission Matrix

    FROM → TO          model    schema    service    router    utils    core
    ─────────────────────────────────────────────────────────────────────
    same domain          ✅       ✅       ✅         ✅        ✅       ✅
    other domain model   ✅       ✅       ✅         ✅        ✅       ✅
    other domain schema  ✅       ✅       ✅         ✅        ✅       ✅
    other domain service ❌       ❌       ❌         ❌        N/A      N/A
    other domain router  ❌       ❌       ❌         ❌        N/A      N/A
    utils                ✅       ✅       ✅         ✅         —       ✅
    core                 ✅       ✅       ✅         ✅        ✅        —

## 5. SQLAlchemy 2.0 Convention

    Models use 2.0 style throughout:
      - Mapped[type] + mapped_column()     (not Column())
      - select() + execute()               (not session.query())
      - DeclarativeBase                    (not declarative_base())
      - Type hints on every column

## 6. Async Convention

    ALL database operations are async:
      - repositories: async def + await db.execute()
      - services:     async def + await repo.method()
      - endpoints:    async def + await service.method()
      - get_db():     async def + await db.commit()/db.rollback()

## 7. base_all.py Import Order

    FK dependency order (a table's FK targets must be imported before it):

    Tier 0: users (no deps)
    Tier 1: properties, payments, notifications, search, consent, media, kyc, reports, admin
    Tier 2: features, analytics, availability, messaging
    Tier 3: bookings, subscriptions
    Tier 4: reviews, agent_reviews
    Tier 5: kyc_admin_reviews

## 8. __init__.py Exports

    Domains WITH models    → export models + schemas only
    Domains WITHOUT models → export schemas only
    Never exported         → service, repository, router

## 9. Enforcement (Automated Tests)

    Run: pytest tests/test_architecture_rules.py -v
    Run: pytest tests/test_import_rules.py -v

    Tests check:
    1. No db operations (query/add/flush/commit/execute) in service files
    2. No commit/rollback in repository files
    3. No db operations in router files
    4. No cross-domain service imports

## 10. Logging Standards

    Use logger, never print().

    Levels:
      logger.debug()     — development diagnostics
      logger.info()      — normal operations (user registered, property created)
      logger.warning()   — unexpected but handled (rate limit hit)
      logger.error()     — operation failed (payment failed), use exc_info=True
      logger.critical()   — system failure (DB down)

    Always include context via extra={}:
      logger.info("Property created", extra={"property_id": prop.id, "owner_id": user.id})

    NEVER log: password, password_hash, jwt_token, refresh_token, otp, card_details

## 11. Error Handling Standards

    Exception hierarchy:
      BaseAppException
      ├── ValidationException      (400 — request data invalid)
      ├── UnauthorizedException    (401 — missing/invalid auth)
      ├── ForbiddenException       (403 — insufficient permissions)
      ├── NotFoundException        (404 — resource not found)
      ├── ConflictException        (409 — state conflict)
      ├── RateLimitException       (429 — too many requests)
      ├── PaymentException         (402 — payment failed)
      └── KYCException             (403 — KYC required/failed)

    Always use:
      raise NotFoundException(message="Property not found")

    Never use:
      raise Exception("Property not found")

## 12. Naming Standards

    Functions:  verb + noun, snake_case    get_user(), create_property(), approve_listing()
    Classes:    PascalCase, Singular       UserService, PropertyRepository, PropertyRead
    Files:      snake_case                 users/service.py (folder provides context)

## 13. Typing Standards

    Type hints required on every function signature.
    Models use Mapped[type] for column typing.
    Never: def get_user(user_id):

## 14. Documentation Standards

    Every public function gets a docstring.
    Never use inline comments as documentation.

## 15. API Endpoint Standards

    Base URL: /api/v1

    Auth:           POST /auth/register, /auth/login, /auth/refresh, /auth/logout
    Users:          GET /users/me, PATCH /users/me, GET /users/{id}
    Properties:     GET /properties, GET /properties/{id}, POST /properties,
                    PATCH /properties/{id}, DELETE /properties/{id}
    Bookings:       POST /bookings, GET /bookings, PATCH /bookings/{id}
    Admin:          POST /admin/properties/{id}/approve, /admin/properties/{id}/reject,
                    POST /admin/users/{id}/suspend

## 16. HTTP Method Rules

    GET     — Read data (idempotent)
    POST    — Create data (not idempotent)
    PUT     — Full replacement (idempotent)
    PATCH   — Partial update (idempotent)
    DELETE  — Soft delete (idempotent)

## 17. Response Standards

    Success:
      {"success": true, "message": "Property created", "data": {"id": "...", ...}}

    Error:
      {"success": false, "message": "Validation failed", "errors": {"price": ["Must be > 0"]}}

    Paginated:
      {"success": true, "data": {"items": [...], "page": 1, "per_page": 20, "total": 500, "pages": 25}}

    Cursor (infinite scroll):
      {"items": [...], "next_cursor": "abc123", "has_more": true}

    Schema: app/shared/schemas.py
      SuccessResponse[T], ErrorResponse, PaginatedResponse[T], CursorPage[T]

## 18. Authentication Standards

    Access Token:   JWT, 30 minutes, in Authorization header
    Refresh Token:  JWT, 7 days, in secure HTTP-only cookie

    Protected endpoints use:
      async def endpoint(current_user: User = Depends(get_current_user))

## 19. Authorization Standards

    Roles:
      User roles:    renter | agent | admin
                   (agent covers both agents and landlords)
      Admin roles:   super_admin | admin | moderator

    Use decorator pattern:
      @require_roles(Role.ADMIN)
      async def approve_listing(...)

## 20. Database Standards

    Primary Keys:   UUID (all tables)
    Audit Fields:   created_at, updated_at (all major tables via TimestampMixin)
    Soft Deletes:   Not used — status fields handle this:
                      Users: suspended_at
                      Properties: approval_status = 'archived'
                      Bookings: status = 'cancelled'
                      Payments: status = 'refunded'

    Never physically delete: Users, Properties, Bookings, Payments

## 21. Security Standards

    Passwords:      bcrypt (passlib). Never: md5, sha1
    Rate Limiting:  Login 5/min, Register 5/min, Search 60/min, Default 100/min
    File Uploads:   Allow: jpg, jpeg, png, webp. Reject: exe, bat, js

## 22. Background Job Standards

    Use: Celery + Redis. Never run these synchronously.
    Approved jobs: SendEmail, PropertyExpiry, SubscriptionRenewal,
                   ImageOptimization, Notification

## 23. Audit Standards

    Every admin action logged in ADMIN_AUDIT_LOG.
    Logged actions: approve_property, reject_property, suspend_user,
                    verify_kyc, delete_listing

## 24. DevOps Standards

    Docker:   Separate images: api, worker, beat, nginx
    Monitoring: Request count, response time, error rate, CPU, memory,
                DB connections, queue length, active users
