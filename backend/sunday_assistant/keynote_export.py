"""Native Keynote export for the local macOS Gmail worker."""
import io
from pathlib import Path
import subprocess
import tempfile
import zipfile

from lxml import etree

from .gmail_source import MAX_ATTACHMENT


class KeynoteExportError(RuntimeError):
    pass


def validate_keynote(data):
    if not data or len(data) > MAX_ATTACHMENT:
        raise ValueError('Keynote 附件空白或超過 35 MB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            entries = package.infolist()
            if len(entries) > 20000 or sum(item.file_size for item in entries) > 300 * 1024 * 1024:
                raise ValueError('Keynote 附件解壓大小超出上限')
            if 'Index/Document.iwa' not in package.namelist():
                raise ValueError('附件不是可辨識的 Keynote 簡報')
    except zipfile.BadZipFile as exc:
        raise ValueError('附件不是完整的 Keynote 簡報') from exc


def export_keynote(data):
    with tempfile.TemporaryDirectory(prefix='sunday-keynote-') as directory:
        original = Path(directory) / 'source.key'
        output = Path(directory) / 'export.pptx'
        original.write_bytes(data)
        try:
            result = subprocess.run(
                ['/usr/bin/osascript', '-e', '''
on run argv
    with timeout of 45 seconds
        tell application id "com.apple.iWork.Keynote"
            set sermonDeck to open (POSIX file (item 1 of argv))
            try
                set slideTotal to count slides of sermonDeck
                export sermonDeck to (POSIX file (item 2 of argv)) as Microsoft PowerPoint
            on error failureMessage number failureNumber
                close sermonDeck saving no
                error failureMessage number failureNumber
            end try
            close sermonDeck saving no
            return slideTotal
        end tell
    end timeout
end run
''', str(original), str(output)], capture_output=True, text=True, timeout=60, check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise KeynoteExportError('Keynote 匯出逾時或無法啟動') from exc
        if result.returncode or not output.is_file():
            detail = result.stderr.strip().splitlines()
            raise KeynoteExportError('Keynote 匯出失敗' + (f'（{detail[-1][:180]}）' if detail else ''))
        exported = output.read_bytes()
        from .gmail_inbox import validate_ppt
        try:
            validate_ppt(exported, '.pptx')
            with zipfile.ZipFile(io.BytesIO(exported)) as package:
                root = etree.fromstring(package.read('ppt/presentation.xml'),
                                       parser=etree.XMLParser(resolve_entities=False, no_network=True))
                slides = root.findall('{*}sldIdLst/{*}sldId')
                if not slides or len(slides) != int(result.stdout.strip()):
                    raise ValueError('投影片張數不一致')
        except ValueError as exc:
            raise KeynoteExportError(f'Keynote 匯出核對失敗（{exc}）') from exc
        return exported
