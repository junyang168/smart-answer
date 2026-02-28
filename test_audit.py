import urllib.parse
from backend.api.sermon_converter_service import audit_sermon_draft

project_id = "_12章_-_安息日的爭議，褻瀆聖靈的罪"
print(f"Testing audit for project: {project_id}")

try:
    result = audit_sermon_draft(project_id)
    print("================== RESULT ==================")
    print(result)
except Exception as e:
    import traceback
    traceback.print_exc()
