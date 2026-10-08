import json
import uuid

from models import OperationLog


def record_operation(db, store_id: str, entity_type: str, entity_id: str, action: str, summary: str, details=None):
    db.add(OperationLog(
        id=str(uuid.uuid4()),
        store_id=store_id,
        entity_type=entity_type,
        entity_id=entity_id or "",
        action=action,
        summary=summary,
        details=json.dumps(details or {}, ensure_ascii=False),
    ))
