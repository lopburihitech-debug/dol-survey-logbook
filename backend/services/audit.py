"""บันทึก Audit Log ตามหัวข้อ 6 ของ Blueprint — ครอบคลุมการเพิ่ม แก้ไข ลบ เปลี่ยนสถานะ และเข้าถึงข้อมูลอ่อนไหว"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _safe_json(data: Any) -> Optional[str]:
    if data is None:
        return None
    try:
        return json.dumps(data, default=str, ensure_ascii=False)
    except TypeError:
        return str(data)


def log_action(
    conn,
    user_id: Optional[str],
    action: str,
    entity: str,
    entity_id: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    ip_address: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO audit_logs (id, user_id, action, entity, entity_id, before_data, after_data, ip_address, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            user_id,
            action,
            entity,
            entity_id,
            _safe_json(before),
            _safe_json(after),
            ip_address,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
