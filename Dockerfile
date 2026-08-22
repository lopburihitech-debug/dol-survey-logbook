# DOL Survey Logbook — image เดียวรันทั้ง API และหน้าเว็บ (เหมาะสำหรับ deploy 1 container ต่อ 1 สำนักงาน/สาขา)
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

WORKDIR /app/backend
RUN chmod +x entrypoint.sh

ENV DATABASE_PATH=/app/backend/data/dol_survey_logbook.db
EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
