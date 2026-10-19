"""数据接入路由：Excel/CSV 上传、外部库同步、API 拉取。"""
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import DbDep, UserDep, require_roles
from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models.identity import Role
from app.db.models.ops import DataSource, IngestJob, SourceType
from app.modules.ingestion.connectors import build_connector
from app.modules.ingestion.service import IngestionService

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["ingestion"])

_UPLOAD_ROOT = Path(settings.UPLOAD_DIR)
_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

MAX_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024


class SourceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: SourceType
    config: dict[str, Any]
    field_mapping: dict[str, str] = Field(default_factory=dict)


class SourceOut(BaseModel):
    id: str
    name: str
    type: SourceType
    config: dict[str, Any]
    field_mapping: dict[str, str]
    enabled: bool
    last_run_at: datetime | None
    last_status: str | None

    model_config = {"from_attributes": True}


@router.get("/datasources", response_model=list[SourceOut])
def list_sources(session: DbDep, _: UserDep):
    rows = session.query(DataSource).order_by(DataSource.created_at.desc()).all()
    return [SourceOut.model_validate(r) for r in rows]


@router.post("/datasources", response_model=SourceOut, status_code=201)
def create_source(
    payload: SourceCreateIn,
    session: DbDep,
    user: Annotated[object, Depends(require_roles(Role.ADMIN, Role.ANALYST))],
):
    source = DataSource(
        id=f"src-{uuid4().hex[:12]}",
        name=payload.name,
        type=payload.type,
        config=payload.config,
        field_mapping=payload.field_mapping,
        created_by=getattr(user, "id", None),
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return SourceOut.model_validate(source)


@router.delete("/datasources/{source_id}")
def delete_source(
    source_id: str,
    session: DbDep,
    _: Annotated[object, Depends(require_roles(Role.ADMIN, Role.ANALYST))],
):
    source = session.get(DataSource, source_id)
    if source is None:
        raise NotFoundError("数据源")
    session.delete(source)
    session.commit()
    return {"message": "已删除"}


@router.post("/ingest/preview")
async def preview_upload(
    session: DbDep,
    _: UserDep,
    file: UploadFile = File(...),
):
    """只看前 5 行与推断出的列映射，不落库——让用户先确认映射对不对。"""
    path = await _save_upload(file)
    from app.modules.ingestion.connectors import FileConnector

    df = FileConnector(str(path)).fetch()
    from app.modules.ingestion.service import infer_mapping

    try:
        mapping = infer_mapping(list(df.columns))
    except ValidationError as exc:
        mapping = {"_error": str(exc)}

    return {
        "temp_path": str(path),
        "filename": file.filename,
        "columns": list(df.columns),
        "rows_total": len(df),
        "preview": df.head(5).fillna("").to_dict(orient="records"),
        "inferred_mapping": mapping,
    }


@router.post("/ingest/upload")
async def ingest_upload(
    session: DbDep,
    user: UserDep,
    file: UploadFile = File(...),
    source_name: str = Form(""),
    replace_existing: bool = Form(False),
    temp_path: str = Form(""),
):
    """上传并入库。支持直接给已预览过的临时路径，避免重复上传大文件。"""
    path = Path(temp_path) if temp_path else await _save_upload(file)
    if not path.exists():
        raise ValidationError("上传文件已失效，请重新上传")

    from app.modules.ingestion.connectors import FileConnector

    df = FileConnector(str(path)).fetch()

    name = source_name or file.filename or "本地文件"
    source_id = f"src-{uuid4().hex[:12]}"
    source = DataSource(
        id=source_id,
        name=name,
        type=SourceType.FILE,
        config={"path": str(path)},
        created_by=user.id,
    )
    session.add(source)
    session.flush()

    result = IngestionService(session).ingest(
        df,
        datasource_id=source_id,
        datasource_name=name,
        replace_existing=replace_existing,
    )
    return {**result, "datasource_id": source_id}


@router.post("/ingest/run/{source_id}")
def run_source(
    source_id: str,
    session: DbDep,
    _: Annotated[object, Depends(require_roles(Role.ADMIN, Role.ANALYST))],
    replace_existing: bool = False,
):
    """按数据源配置重新拉取并入库（数据库源与 API 源走这里）。"""
    source = session.get(DataSource, source_id)
    if source is None:
        raise NotFoundError("数据源")

    connector = build_connector(source.type.value, source.config)
    df = connector.fetch()
    return IngestionService(session).ingest(
        df,
        datasource_id=source.id,
        datasource_name=source.name,
        field_mapping=source.field_mapping,
        replace_existing=replace_existing,
    )


@router.get("/ingest/jobs")
def list_jobs(session: DbDep, _: UserDep, limit: int = Query(20, ge=1, le=200)):
    rows = (
        session.query(IngestJob)
        .order_by(IngestJob.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "datasource_name": r.datasource_name,
            "status": r.status.value,
            "rows_read": r.rows_read,
            "rows_written": r.rows_written,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]


async def _save_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls"}:
        raise ValidationError("仅支持 csv / xlsx / xls 文件")

    target = _UPLOAD_ROOT / f"{uuid4().hex}{suffix}"
    size = 0
    with target.open("wb") as fp:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                fp.close()
                target.unlink(missing_ok=True)
                raise ValidationError(f"文件超过 {settings.MAX_UPLOAD_MB}MB 限制")
            fp.write(chunk)
    return target
