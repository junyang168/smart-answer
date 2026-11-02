"""One source for dated OneDrive PPT storage and the desktop folder link.

OneDrive's Mac sync client uploads these files; a successful filesystem write
does not prove that the cloud copy is available yet.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("America/Chicago")


def service_day(value: str) -> date:
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise ValueError("無效的主日日期")


def folder_name(day: date) -> str:
    return f"{day.month}-{day.day}-{day.year}"


def config_path() -> Path:
    return Path(os.getenv("SUNDAY_PRESENTER_CONFIG") or
                str(Path(os.environ["DATA_BASE_DIR"]) / "config" / "sunday_presenter.json"))


@dataclass(frozen=True)
class OneDrivePpts:
    root: Path
    web_root_url: str

    @classmethod
    def configured(cls):
        path = config_path()
        if not path.exists():
            return None  # Existing installs retain local storage until configured.
        try:
            data = json.loads(path.read_text())
            root = Path(data["syncRoot"]).expanduser()
            web = urlsplit(data["webRootUrl"])
            query = dict(parse_qsl(web.query))
            if (not root.is_absolute() or web.scheme != "https" or
                    web.netloc != "onedrive.live.com" or web.path != "/my" or
                    not query.get("id", "").startswith("/personal/") or
                    any(part in {".", ".."} for part in query["id"].split("/"))):
                raise ValueError()
            return cls(root, urlunsplit(("https", web.netloc, web.path,
                                        urlencode({"id": query["id"].rstrip("/")}), "")))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("OneDrive 主日 PPT 設定無效，請檢查同步目錄及網頁根目錄連結") from exc

    def folder(self, day: date) -> Path:
        # Never manufacture a disconnected substitute when OneDrive is absent.
        if not self.root.is_dir():
            raise OSError("OneDrive 主日同步目錄不可用，請確認 OneDrive 已登入並啟動")
        root = self.root.resolve()
        target = root / folder_name(day)
        if target.resolve().parent != root:
            raise ValueError("主日資料夾不可連結到 OneDrive 根目錄以外")
        return target

    def file(self, day: date, filename: str) -> Path:
        if not filename or Path(filename).name != filename or not filename.lower().endswith(".pptx"):
            raise ValueError("無效的 PPT 檔名")
        target = self.folder(day) / filename
        if target.is_symlink():
            raise ValueError("PPT 不可使用符號連結")
        return target

    def generated(self, day: date) -> Path:
        return self.file(day, f"主日崇拜-{day.isoformat()}-自動生成.pptx")

    def url(self, day: date) -> str:
        web = urlsplit(self.web_root_url)
        root_id = dict(parse_qsl(web.query))["id"]
        return urlunsplit((web.scheme, web.netloc, web.path,
                           urlencode({"id": root_id + "/" + folder_name(day)}), ""))

    def publish_generated(self, day: date, source: Path) -> Path:
        target = self.generated(day)
        target.parent.mkdir(exist_ok=True)
        # Keep the previous complete PPT if copying fails. Never move/delete the
        # assistant's immutable local artifact or a speaker's separate file.
        fd, temporary = tempfile.mkstemp(prefix=".ppt-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream, source.open("rb") as original:
                shutil.copyfileobj(original, stream)
            Path(temporary).replace(target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target


def upcoming_folder(now: datetime | None = None) -> dict:
    local = (now or datetime.now(ZONE)).astimezone(ZONE)
    day = local.date() + timedelta(days=(6 - local.weekday()) % 7)
    storage = OneDrivePpts.configured()
    if storage is None:
        raise ValueError("網站尚未設定 OneDrive 主日 PPT 目錄")
    folder = storage.folder(day)
    return {"date": day.isoformat(), "timezone": str(ZONE),
            "folderUrl": storage.url(day), "folderName": folder_name(day),
            "folderExists": folder.is_dir(), "storage": "onedrive-sync",
            "cloudStatus": "not_verified"}
