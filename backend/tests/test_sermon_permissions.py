from backend.api.sc_api.sermon_manager import SermonManager


def test_no_permission_includes_all_permission_fields():
    manager = SermonManager.__new__(SermonManager)

    permission = manager.get_no_permission()

    assert permission.canRead is False
    assert permission.canWrite is False
    assert permission.canAssign is False
    assert permission.canUnassign is False
    assert permission.canAssignAnyone is False
    assert permission.canPublish is False
    assert permission.canViewPublished is False
