# 06 — Persistence and Configuration

Everything below comes from `app/insights/config/` and `app/insights/repository/` as currently on disk.

---

## Settings system — `app/insights/config/settings.py`

`InsightSettings` is a `pydantic_settings.BaseSettings` subclass. It reads environment variables prefixed with `VOICEIQ_` and supports a `.env` file in the repo root.

### Fields

| Field | Type | Default | Env var |
|-------|------|---------|---------|
| `service_name` | `str` | `"voiceiq-insight-service"` | `VOICEIQ_SERVICE_NAME` |
| `service_version` | `str` | `"1.0.0"` | `VOICEIQ_SERVICE_VERSION` |
| `environment` | `Literal["dev", "staging", "prod", "test"]` | `"dev"` | `VOICEIQ_ENVIRONMENT` |
| `log_level` | `str` | `"INFO"` | `VOICEIQ_LOG_LEVEL` |
| `database_url` | `str` | `"sqlite:///./data/insights.db"` | `VOICEIQ_DATABASE_URL` |
| `database_echo` | `bool` | `False` | `VOICEIQ_DATABASE_ECHO` |
| `database_auto_create` | `bool` | `True` | `VOICEIQ_DATABASE_AUTO_CREATE` |
| `threshold_profile` | `Literal["default", "strict", "lenient"]` | `"default"` | `VOICEIQ_THRESHOLD_PROFILE` |
| `api_max_session_payload_kb` | `int` | `2048` | `VOICEIQ_API_MAX_SESSION_PAYLOAD_KB` |

### `model_config`

```python
model_config = SettingsConfigDict(
    env_prefix="VOICEIQ_",
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",
)
```

`extra="ignore"` is deliberate: unrecognised env vars in `.env` do not raise.

### `get_settings()`

```python
@lru_cache(maxsize=1)
def get_settings() -> InsightSettings:
    return InsightSettings()
```

The cache is what makes settings effectively a singleton across the process. Tests that need to override settings call `get_settings.cache_clear()` first.

### `.env.example`

A documented `.env.example` lives at the repo root. Every env var carries a comment describing its purpose and the runtime impact of overriding it.

---

## Threshold profiles — `app/insights/config/defaults.py`

`InsightThresholds` is a Pydantic model with the following fields and defaults. These knobs are read by every detector that thresholds against them.

| Field | Default |
|-------|---------|
| `dominance_speaking_ratio_threshold` | `0.60` |
| `dominance_word_ratio_threshold` | `0.60` |
| `engagement_drop_pause_threshold_sec` | `3.0` |
| `severe_engagement_drop_pause_threshold_sec` | `6.0` |
| `high_tension_interruption_threshold` | `2` |
| `high_tension_overlap_threshold` | `2` |
| `emotional_shift_delta_threshold` | `0.45` |
| `severe_emotional_shift_delta_threshold` | `0.70` |
| `frequent_interruptions_threshold` | `2` |
| `high_overlap_participation_threshold` | `2` |
| `low_inquiry_min_utterances` | `4` |

```python
DEFAULT_THRESHOLDS = InsightThresholds()
```

### `get_thresholds_for_profile(profile)`

Returns the `InsightThresholds` instance for `"default"`, `"strict"`, or `"lenient"`. Unknown profiles fall back to `DEFAULT_THRESHOLDS`. The strict profile lowers the dominance threshold and tightens the emotional-shift delta; the lenient profile inverts those changes.

The threshold profile name is plumbed end-to-end: it is passed in the `meta.threshold_profile` field on every `InsightGenerateResponse`.

---

## Database engine — `app/insights/repository/db.py`

The persistence layer is SQLAlchemy 2.0 with the typed-ORM API.

### Module globals

```python
class Base(DeclarativeBase):
    pass

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None
```

The engine and session factory are lazily initialised on first call to `init_engine()` or `get_session_factory()`. They can be reset with `reset_engine()` (used by tests to swap to a fresh per-test SQLite file).

### `init_engine(settings)`

Creates the SQLAlchemy `Engine`. Behaviour:

- For SQLite URLs, calls `_ensure_sqlite_directory(url)` to `mkdir -p` the parent directory of the database file. This is what guarantees `./data/insights.db` works on first start.
- For SQLite URLs, passes `connect_args={"check_same_thread": False}` to permit FastAPI's threadpool to share the connection pool.
- `echo` is wired to `settings.database_echo`.

### `get_engine()` / `get_session_factory()`

Lazy accessors. Both call `init_engine(get_settings())` if the globals are not yet set.

### `init_db(settings)`

```python
def init_db(settings: InsightSettings) -> None:
    if not settings.database_auto_create:
        return
    engine = init_engine(settings)
    Base.metadata.create_all(bind=engine)
```

This is what the FastAPI `lifespan` calls on startup. Setting `VOICEIQ_DATABASE_AUTO_CREATE=false` is the way to opt out of automatic schema creation in environments where migrations are managed externally.

### `session_scope()` context manager

```python
@contextmanager
def session_scope() -> Iterator[Session]:
    session_factory = get_session_factory()
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

Every repository call uses this. Commit on success, rollback on exception, always close.

### `get_db()` dependency

```python
def get_db() -> Iterator[Session]:
    with session_scope() as session:
        yield session
```

Available as a FastAPI `Depends` for callers that want a raw SQLAlchemy session. The repository layer wraps it.

### CLI bootstrap

The module is runnable as a script:

```bash
python -m app.insights.repository.db
```

This calls `init_db(get_settings())` and prints the resolved database URL. Used to bootstrap the schema before first run in environments where the lifespan is not invoked (e.g. one-shot migration containers).

---

## ORM model — `app/insights/repository/orm_models.py`

A single table backs the entire service.

```python
class InsightRecordORM(Base):
    __tablename__ = "insight_records"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow,
    )
```

`_utcnow()` returns `datetime.now(timezone.utc)`. `to_dict()` returns the listing-row shape used by the listing endpoints (excludes `payload_json`).

The schema is intentionally minimal: full payloads are stored as a single JSON string in `payload_json`. The application is responsible for reading and writing the structured `InsightStoredRecord` shape; the database treats it as opaque text.

---

## Repository — `app/insights/repository/insight_repository.py`

`InsightRepository` wraps the ORM with a typed `InsightStoredRecord` interface.

### Constructor

```python
def __init__(self, session_factory: Optional[sessionmaker] = None) -> None:
    self._session_factory = session_factory  # if None, uses module-level factory
```

Tests pass an isolated `sessionmaker` bound to a per-test SQLite file.

### Public methods

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| `save` | `save(record: InsightStoredRecord) -> InsightStoredRecord` | Upsert by `session_id`. Returns the stored record. |
| `get` | `get(session_id: str) -> Optional[InsightStoredRecord]` | Returns `None` if absent. |
| `exists` | `exists(session_id: str) -> bool` | Cheap existence check (no payload deserialisation). |
| `delete` | `delete(session_id: str) -> bool` | `True` if a row was removed, `False` otherwise. |
| `list_session_ids` | `list_session_ids() -> List[str]` | Ordered by `updated_at DESC`. |
| `list_records` | `list_records() -> List[InsightStoredRecord]` | Hydrates every payload. Avoid for large datasets. |
| `count` | `count() -> int` | Total row count. |
| `clear` | `clear() -> int` | Truncates the table. Returns the deleted row count. Used by tests. |

### Hydration

```python
def _to_stored(self, row: InsightRecordORM) -> InsightStoredRecord:
    return InsightStoredRecord(
        session_id=row.session_id,
        status=row.status,
        payload=InsightGenerateResponse.model_validate_json(row.payload_json),
    )
```

Pydantic v2's `model_validate_json` is what makes the round trip cheap: payloads are stored as the response's serialised JSON and rehydrated directly into the typed model.

### Error wrapping

All SQLAlchemy errors raised inside repository methods are wrapped in `InsightRepositoryError` with the original exception preserved as the cause. Callers (the API layer, the service) catch this and translate it to HTTP 500.

### Module exports

`app/insights/repository/__init__.py` exports:

```python
from app.insights.repository.insight_repository import (
    InsightRepository,
    InsightRepositoryError,
)

insight_repository = InsightRepository()  # module-level singleton

def get_insight_repository() -> InsightRepository:
    return insight_repository
```

`get_insight_repository` is the FastAPI dependency. Tests override it with `app.dependency_overrides[get_insight_repository] = lambda: isolated_repo`.

---

## Persistence shape

`InsightStoredRecord(session_id: str, status: str, payload: InsightGenerateResponse)` is the unit of storage. The `payload` round-trips identically: the integration test `test_get_after_generate_round_trip` asserts that the body returned from `GET /v1/insights/{session_id}` is byte-equivalent to what `POST /v1/insights/generate` returned.

The choice to store the full response (rather than reconstructing it on read) is deliberate. It keeps the read path O(1) and isolates the storage schema from changes to detector logic — bumping a threshold in the rules layer does not invalidate stored records.

---

## Configuration overrides at runtime

The two most useful overrides for operators:

- `VOICEIQ_DATABASE_URL` — point at any SQLAlchemy-supported DB. PostgreSQL is supported but the default ORM uses SQLite-compatible types; switching to PG should work with `psycopg2-binary` installed.
- `VOICEIQ_THRESHOLD_PROFILE` — flip the global rules profile. The profile is also visible in every response's `meta.threshold_profile`, so downstream consumers can reason about what produced the bundle.

Other overrides (`VOICEIQ_DATABASE_ECHO`, `VOICEIQ_LOG_LEVEL`) are diagnostics only and should not be enabled in production.
