"""Listing drafts and human review only; independent of the pricing board."""
import base64
import binascii
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.store_routes import get_store_id
from database import get_db
from services.listing_workbench import (ListingDraft, EDITABLE_FIELDS, MEASUREMENTS, MAX_BYTES,
                                       colours, colour, issues, merge_import, now, number,
                                       parse_table, review_export, serialize)

router = APIRouter(prefix='/api/listing', tags=['listing'])


class ImportTable(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1, max_length=200)
    data_base64: str = Field(max_length=(MAX_BYTES + 2) // 3 * 4)


class Patch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    fields: dict


class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)


class ExportRows(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rows: dict[str, int] = Field(min_length=1, max_length=100)


def find(db, store, draft_id):
    row = db.query(ListingDraft).filter_by(id=draft_id, store_id=store).first()
    if row is None:
        raise HTTPException(404, '当前店铺没有这条上架草稿')
    return row


def change(db, row, revision, **values):
    statement = update(ListingDraft).where(ListingDraft.id == row.id, ListingDraft.store_id == row.store_id,
                                           ListingDraft.revision == revision).values(
        **values, revision=revision + 1, updated_at=now())
    if db.execute(statement.execution_options(synchronize_session=False)).rowcount != 1:
        db.rollback()
        raise HTTPException(409, '资料已被更新，请刷新后再操作，未覆盖他人的修改')
    db.commit()
    db.refresh(row)
    return serialize(row)


@router.get('/drafts')
def drafts(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
           search: str = Query('', max_length=100), state: Literal['', 'draft', 'reviewed'] = '',
           db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    query = db.query(ListingDraft).filter_by(store_id=store)
    if search:
        query = query.filter(or_(ListingDraft.internal_code.contains(search, autoescape=True),
                                 ListingDraft.payload.contains(search, autoescape=True)))
    if state:
        query = query.filter_by(state=state)
    total = query.count()
    items = query.order_by(ListingDraft.updated_at.desc(), ListingDraft.id).offset((page-1)*page_size).limit(page_size).all()
    return dict(items=[serialize(r) for r in items], total=total, page=page, page_size=page_size)


@router.post('/import')
def import_table(data: ImportTable, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    try:
        raw = base64.b64decode(data.data_base64, validate=True)
        # Reject paths and discard anything resembling a local filesystem prefix.
        name = data.filename.replace('\\', '/').split('/')[-1]
        rows = parse_table(raw, name)
    except (ValueError, binascii.Error) as e:
        raise HTTPException(400, str(e)) from None
    existing = {r.internal_code: r for r in db.query(ListingDraft).filter_by(store_id=store).all()}
    created = updated = conflicts = unchanged = 0
    for item in rows:
        row = existing.get(item['internal_code'])
        if row is None:
            db.add(ListingDraft(store_id=store, internal_code=item['internal_code'], source_file=item['source_file'],
                                source_row=item['source_row'], payload=json.dumps(item['payload'], ensure_ascii=False),
                                updated_at=now()))
            created += 1
        else:
            before = json.loads(row.payload)
            try:
                merged = merge_import(before, item['payload'])
            except ValueError as e:
                db.rollback()
                raise HTTPException(409, str(e)) from None
            if merged == before:
                unchanged += 1
                continue
            conflicts += bool(merged['import_conflicts'])
            revision = row.revision
            count = db.execute(update(ListingDraft).where(ListingDraft.id == row.id, ListingDraft.store_id == store,
                               ListingDraft.revision == revision).values(payload=json.dumps(merged, ensure_ascii=False),
                               state='draft', reviewed_at=None, revision=revision+1, updated_at=now())
                               .execution_options(synchronize_session=False)).rowcount
            if count != 1:
                db.rollback()
                raise HTTPException(409, '导入期间草稿被修改，本次导入已取消，请重试')
            updated += 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '另一批导入已创建相同内部编号，请刷新后重试') from None
    return dict(created=created, updated=updated, unchanged=unchanged, conflicts=conflicts, count=len(rows))


@router.get('/drafts/{draft_id}')
def get_draft(draft_id: str, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    return serialize(find(db, store, draft_id))


@router.patch('/drafts/{draft_id}')
def patch(draft_id: str, data: Patch, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    row = find(db, store, draft_id)
    if set(data.fields) - EDITABLE_FIELDS:
        raise HTTPException(400, '包含不可修改字段；编号、原文及审核状态不能通过资料编辑改写')
    payload = json.loads(row.payload)
    try:
        for field, value in data.fields.items():
            if field in MEASUREMENTS:
                payload[field] = number(value, field)
            elif field == 'other_colours':
                if not isinstance(value, list) or len(value) > 20 or not all(isinstance(x, str) for x in value):
                    raise ValueError('Other Colours必须为颜色列表，最多20项')
                payload[field] = colours(value)
            elif field == 'image_urls':
                if not isinstance(value, list) or len(value) > 20 or not all(isinstance(x, str) and len(x) <= 2000 for x in value):
                    raise ValueError('图片链接必须为列表，最多20张')
                payload[field] = [x.strip() for x in value if x.strip()]
            else:
                if not isinstance(value, str) or len(value) > 12000:
                    raise ValueError('文本字段须为字符串，且不能超过12000字符')
                payload[field] = colour(value) if field == 'major_colour' else value.strip()
        # A conflict is resolved only when that field was explicitly checked/saved.
        payload['import_conflicts'] = [c for c in payload.get('import_conflicts', []) if c['field'] not in data.fields]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return change(db, row, data.revision, payload=json.dumps(payload, ensure_ascii=False), state='draft', reviewed_at=None)


@router.post('/drafts/{draft_id}/review')
def review(draft_id: str, data: Review, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    row = find(db, store, draft_id)
    if row.revision != data.revision:
        raise HTTPException(409, '资料已更新，请重新查看后审核')
    problems = issues(json.loads(row.payload))
    if problems:
        raise HTTPException(400, '资料尚未齐全：' + '；'.join(p['message'] for p in problems))
    return change(db, row, data.revision, state='reviewed', reviewed_at=now())


@router.post('/review-export')
def export_review(data: ExportRows, db: Session = Depends(get_db), store: str = Depends(get_store_id)):
    rows = [find(db, store, draft_id) for draft_id in data.rows]
    for row in rows:
        if row.revision != data.rows[row.id] or row.state != 'reviewed' or issues(json.loads(row.payload)):
            raise HTTPException(409, '包含未审核、已修改或资料不完整的草稿，请重新核对')
    # No prices, seller API calls or fake Submitted/Approved status transitions.
    content = review_export(rows)
    return Response(content, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': 'attachment; filename="listing-reviewed-data.xlsx"'})
