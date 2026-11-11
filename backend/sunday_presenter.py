"""One source for dated OneDrive PPT storage and the desktop folder link.

OneDrive's Mac sync client uploads these files; a successful filesystem write
does not prove that the cloud copy is available yet.
"""
from dataclasses import dataclass
from hashlib import sha256
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from backend.sunday_data_lock import sunday_write

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
        return self.file(day, f"聖道教會{day.isoformat()}主日崇拜.pptx")

    def url(self, day: date) -> str:
        web = urlsplit(self.web_root_url)
        root_id = dict(parse_qsl(web.query))["id"]
        return urlunsplit((web.scheme, web.netloc, web.path,
                           urlencode({"id": root_id + "/" + folder_name(day)}), ""))

    def _state_path(self, day: date) -> Path:
        # Keep ownership metadata outside the presentation folder. An existing
        # untracked or manually edited deck is never assumed to be disposable.
        root_key = sha256(str(self.root.resolve()).encode()).hexdigest()[:16]
        return (Path(os.environ["DATA_BASE_DIR"]) / "sunday-assistant" /
                "ppt-publication" / root_key / f"{day.isoformat()}.json")

    def archive_folder(self, day: date, kind: str) -> Path:
        if kind not in {"崇拜PPT版本", "講員PPT版本"}:
            raise ValueError("無效的 PPT 版本目錄")
        parent = self.folder(day).parent
        for name in ("歷史版本", folder_name(day), kind):
            path = parent / name
            if path.is_symlink() or path.resolve().parent != parent.resolve():
                raise ValueError("PPT 版本目錄不可連結到其他目錄")
            path.mkdir(exist_ok=True)
            parent = path
        return parent

    @staticmethod
    def _copy(source: Path, target: Path) -> Path:
        if target.is_symlink():
            raise ValueError("PPT 不可使用符號連結")
        if target.exists() and sha256(source.read_bytes()).digest() == sha256(target.read_bytes()).digest():
            return target
        fd, temporary = tempfile.mkstemp(prefix=".ppt-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream, source.open("rb") as original:
                shutil.copyfileobj(original, stream)
            if sha256(Path(temporary).read_bytes()).digest() != sha256(source.read_bytes()).digest():
                raise OSError("PPT 複製核對失敗，保留原檔")
            Path(temporary).replace(target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target

    def _archive(self, day: date, source: Path) -> Path:
        digest = sha256(source.read_bytes()).hexdigest()
        return self._copy(source, self.archive_folder(day, "崇拜PPT版本") / f"{digest}-{source.name}")

    def _retire_legacy_generated(self, day: date) -> None:
        old = self.file(day, f"主日崇拜-{day.isoformat()}-自動生成.pptx")
        if old.exists():
            self._archive(day, old)
            old.unlink()  # Only after its exact original bytes were preserved.

    @staticmethod
    def _save_state(path: Path, state: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(state) + "\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @sunday_write
    def publish_generated(self, day: date, source: Path) -> Path:
        target = self.generated(day)
        target.parent.mkdir(exist_ok=True)
        state_path = self._state_path(day)
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        owned = (state.get("kind") == "generated" and target.exists() and
                 state.get("sha256") == sha256(target.read_bytes()).hexdigest())
        if target.exists() and not owned:
            # Still allow a new draft to be downloaded, without replacing the
            # human revision used for presentation, final download and email.
            result = self._archive(day, source)
        else:
            result = self._copy(source, target)
            self._save_state(state_path, {"kind": "generated", "sha256": sha256(target.read_bytes()).hexdigest()})
        self._retire_legacy_generated(day)
        return result

    @sunday_write
    def publish_final(self, day: date, source: Path) -> Path:
        target = self.generated(day)
        target.parent.mkdir(exist_ok=True)
        if target.exists() and target.read_bytes() != source.read_bytes():
            self._archive(day, target)
        # Persist manual ownership first: even an identical-byte upload must
        # prevent a later background render from replacing the approved file.
        state_path = self._state_path(day)
        self._save_state(state_path, {"kind": "final"})
        self._copy(source, target)
        self._retire_legacy_generated(day)
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
