import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import copy
import json
import re
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from backend.api.config import CONFIG_DIR, DATA_BASE_PATH
from backend.api.gemini_client import gemini_client

from .access_control import AccessControl
from .copilot import Copilot, ChatMessage, Document
from .image_to_text import ImageToText
from .script_delta import ScriptDelta
from .sermon_comment import SermonCommentManager
from .sermon_meta import Sermon, SermonMetaManager

env_file = os.getenv("ENV_FILE")
if env_file:
    load_dotenv(env_file)
else:
    load_dotenv()  # Fallback to default .env file in the current directory


class Permission(BaseModel):
    canRead: bool
    canWrite: bool
    canAssign: bool
    canUnassign: bool
    canAssignAnyone: bool
    canPublish: bool 
    canViewPublished: bool 
 

#SermanManager is responsible for managing 
# Sermon => sermon metadata
# Sermon Script and Slide  => SermonDelta
# Sermon Permissions => AccessControl
class SermonManager:

    def __init__(self) -> None:


        self.base_folder = str(DATA_BASE_PATH)
        self.config_folder = str(CONFIG_DIR)
        self._acl = AccessControl(self.base_folder)
        self._sm = SermonMetaManager(self.base_folder, self._acl.get_user)
        self._scm = SermonCommentManager()
        self.semantic_search_url = os.getenv('SEMANTIC_SEARCH_API_URL')
        self._audit_fields = (
            "status",
            "assigned_to",
            "assigned_to_name",
            "assigned_to_date",
            "author",
            "author_name",
            "last_updated",
            "published_date",
            "title",
            "summary",
            "keypoints",
            "core_bible_verse",
            "deliver_date",
            "theme",
            "type",
            "source",
        )
        self._audit_log_dir = os.path.join(self.base_folder, "logs")
        self._audit_log_file = os.path.join(self._audit_log_dir, "sermon_meta_audit.log")
        os.makedirs(self._audit_log_dir, exist_ok=True)

        self._quick_search_cache: Dict[str, Tuple[datetime, List[str]]] = {}
        self._quick_search_ttl = timedelta(minutes=10)

        with open(os.path.join(self.config_folder, 'fellowship.json'), 'r', encoding='utf-8') as f:
            self.fellowship = json.load(f)




        refreshers = [self._acl.get_refresher(), self._sm.get_refresher()]
        event_handler = ConfigFileEventHandler(refreshers)
        observer = Observer()
        observer.schedule(event_handler, os.path.dirname(self.config_folder + '/config.json'), recursive=False)
        observer.start()

    def get_next_fellowship(self):
        last_fellowship = self.fellowship[-1] 
        last_date_str = last_fellowship['date'] 
        last_date = datetime.strptime(last_date_str, "%m/%d/%Y")

        # If the last recorded date is already in the future, return it
        if last_date > datetime.now():
             return {'date': last_date.strftime("%m/%d/%Y")}

        next_date = last_date + timedelta(weeks=2)
        if next_date < datetime.now():
            while next_date < datetime.now():
                last_date = next_date
                self.fellowship.append({'date': next_date.strftime("%m/%d/%Y")})
                next_date = last_date + timedelta(weeks=2)
            
            with open(os.path.join(self.config_folder, 'fellowship.json'), 'w', encoding='utf-8') as f:
                json.dump(self.fellowship, f, ensure_ascii=False, indent=4)

        return {'date': next_date.strftime("%m/%d/%Y")}

    def get_file_path(self,type:str, item:str):
        return os.path.join(self.base_folder, type, item + '.json')

  

    def get_sermon_detail(self, user_id:str, item:str, changes:str = None):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canRead:
            return "You don't have permission to read this item"

        sermon = self._sm.get_sermon_metadata(user_id, item)
        if sermon:
            sermon.series_id = self.get_series_id_for_item(item)

        sd = ScriptDelta(self.base_folder, item)
        script =  sd.get_script( changes == 'changes')
        for p in script:
            if p.get('type') == 'comment':
                ui = self.get_user_info( p.get('user_id') )
                if ui:
                    p['user_name'] = ui.get('name')
        return sermon, script
    
    def get_series_id_for_item(self, item: str) -> Optional[str]:
        series_meta_file = os.path.join(self.config_folder, 'sermon_series.json')
        if not os.path.exists(series_meta_file):
            return None
        try:
            with open(series_meta_file, 'r', encoding='utf-8') as f:
                series_list = json.load(f)
        except json.JSONDecodeError:
            return None
            
        for series in series_list:
            if item in series.get('sermons', []):
                return series.get('id')
        return None
    
    def get_slide_text(self, user_id:str, item:str, timestamp:int):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canRead:
            return {"message": "You don't have read permission"}
        
        i2t = ImageToText(item)
        txt = i2t.extract_slide(timestamp)
        return {'text': txt}
        
    def get_slide_image(self, user_id:str, item:str, timestamp:int):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canWrite:
            return {"message": "You don't have read permission"}
        
        i2t = ImageToText(item)
        img_url = i2t.get_slide_image_url(self.base_folder, timestamp)
        return {'image_url': img_url}


    def update_sermon(self, user_id:str, type:str,  item:str, data:dict):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canWrite:
            return {"message": "You don't have permission to update this item"}
        
        #update last updated and author
        self._sm.update_sermon_metadata(user_id, item)

        sd = ScriptDelta(self.base_folder, item)
        return sd.save_script(user_id, type, item,data)

    def update_sermon_header(
        self,
        user_id,
        item: str,
        title: str,
        *,
        summary: Optional[str] = None,
        keypoints: Optional[str] = None,
        core_bible_verse: Optional[List[dict]] = None,
    ):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canWrite:
            return {"message": "You don't have permission to update this item"}
        sermon = self._sm.get_sermon_metadata(user_id, item)
        if not sermon:
            return {"message": "sermon not found"}
        before_snapshot = self._snapshot_sermon_meta(sermon)
        self._sm.update_sermon_metadata(
            user_id,
            item,
            title,
            summary=summary,
            keypoints=keypoints,
            core_bible_verse=core_bible_verse,
        )
        self._sm.save_sermon_metadata()
        self._log_sermon_meta_change(
            user_id,
            item,
            before_snapshot,
            self._snapshot_sermon_meta(sermon),
            context="update_sermon_header",
        )
        return {}

    def generate_sermon_metadata(
        self,
        user_id: str,
        item: str,
        paragraphs: Optional[List[object]] = None,
    ) -> dict:
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canWrite:
            raise PermissionError("You don't have permission to update this item")

        sermon_meta = self._sm.get_sermon_metadata(user_id, item)
        existing_title = sermon_meta.title if sermon_meta and sermon_meta.title else item

        if paragraphs is None or len(paragraphs) == 0:
            sd = ScriptDelta(self.base_folder, item)
            script_payload = sd.get_final_script(False)
            paragraph_source = script_payload.get('script', [])
        else:
            paragraph_source = paragraphs

        compiled_lines: List[str] = []
        for entry in paragraph_source:
            text = getattr(entry, 'text', None)
            if text is None and isinstance(entry, dict):
                text = entry.get('text')
            if not text:
                continue
            text = ScriptDelta.remove_format(text)
            if not text:
                continue
            index_value = getattr(entry, 'index', None)
            if index_value is None and isinstance(entry, dict):
                index_value = entry.get('index')
            prefix = f"[{index_value}] " if index_value else ""
            compiled_lines.append(f"{prefix}{text.strip()}")

        if not compiled_lines:
            raise ValueError("缺少講道內容，無法產生講道資訊。")

        combined_text = "\n\n".join(compiled_lines).strip()
        max_chars = 8000
        if len(combined_text) > max_chars:
            combined_text = combined_text[:max_chars] + "\n...（內容節錄）"

        prompt = (
            "你是一位熟悉聖經與講道寫作的華語牧者助理，"
            "請根據提供的講道內容，總結出新的講道標題、摘要、要點以及核心經文。"
            "請務必使用繁體中文，並且僅輸出 JSON 字串，符合以下格式：\n"
            "{\n"
            '  "title": "...",\n'
            '  "summary": "...",\n'
            '  "keypoints": ["..."],\n'
            '  "core_bible_verse": [\n'
            '    {"book": "...", "chapter_verse": "...", "text": "..."}\n'
            "  ]\n"
            "}\n"
            "規則：\n"
            "- title：10-18 個繁體中文字，呼應講道主題。\n"
            "- summary：少于150字，概括講道重點。\n"
            "- core_bible_verse：最多 3 節經文，若判斷不出可回傳空陣列。\n"
            "- 若缺乏足夠資訊，請以空字串或空陣列表示。\n"
            "- 不要輸出任何解釋或額外文字。\n\n"
            f"講道原始標題：{existing_title}\n"
            "講道內容：\n"
            f"{combined_text}\n"
        )

        raw_response = gemini_client.generate(prompt)
        cleaned_response = raw_response.strip()

        if cleaned_response.startswith("```"):
            segments = cleaned_response.split("```")
            cleaned_response = "".join(segment for segment in segments if segment.strip().startswith("{")) or cleaned_response

        if not cleaned_response.strip().startswith("{"):
            start = cleaned_response.find("{")
            end = cleaned_response.rfind("}")
            if start != -1 and end != -1 and end > start:
                cleaned_response = cleaned_response[start : end + 1]

        try:
            payload = json.loads(cleaned_response)
        except json.JSONDecodeError as exc:
            raise ValueError("AI 回傳結果解析失敗，請稍後再試。") from exc

        title = str(payload.get("title") or existing_title).strip()
        if not title:
            title = existing_title

        summary = str(payload.get("summary") or "").strip()

        keypoints_field = payload.get("keypoints", [])
        if isinstance(keypoints_field, list):
            keypoints_items = [str(item).strip() for item in keypoints_field if str(item).strip()]
            keypoints = "\n".join(f"- {item}" for item in keypoints_items)
        else:
            keypoints = str(keypoints_field or "").strip()

        verses_field = payload.get("core_bible_verse", [])
        normalized_verses: List[dict] = []
        if isinstance(verses_field, list):
            for verse in verses_field:
                if isinstance(verse, dict):
                    book = str(verse.get("book") or "").strip()
                    chapter = str(verse.get("chapter_verse") or "").strip()
                    text = str(verse.get("text") or "").strip()
                    if book or chapter or text:
                        normalized_verses.append({
                            "book": book,
                            "chapter_verse": chapter,
                            "text": text,
                        })
                if len(normalized_verses) >= 3:
                    break

        return {
            "title": title,
            "summary": summary,
            "keypoints": keypoints,
            "core_bible_verse": normalized_verses,
        }

    def get_sermons(self, user_id:str):
        return self._sm.sermons
    

    def _snapshot_sermon_meta(self, sermon: Optional[Sermon]) -> dict:
        if sermon is None:
            return {}
        snapshot = {}
        for field in self._audit_fields:
            snapshot[field] = copy.deepcopy(getattr(sermon, field, None))
        return snapshot

    def _log_sermon_meta_change(
        self,
        actor_id: str,
        item: str,
        before: dict,
        after: dict,
        *,
        context: Optional[str] = None,
    ) -> None:
        if not before and not after:
            return

        changes = {}
        for field in self._audit_fields:
            old_value = before.get(field)
            new_value = after.get(field)
            if old_value != new_value:
                changes[field] = {"old": old_value, "new": new_value}

        if not changes:
            return

        actor_info = self.get_user_info(actor_id) if actor_id else None
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "item": item,
            "actor_id": actor_id,
            "actor_name": actor_info.get("name") if actor_info else None,
            "changes": changes,
        }
        if context:
            entry["context"] = context

        try:
            with open(self._audit_log_file, "a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            # Failing to log should not block core workflow.
            pass

    def get_sermon_audit_log(self, item: str, limit: int = 50) -> List[dict]:
        if limit <= 0:
            return []
        capped_limit = min(limit, 200)
        if not os.path.exists(self._audit_log_file):
            return []

        entries: List[dict] = []
        try:
            with open(self._audit_log_file, "r", encoding="utf-8") as log_file:
                for raw_line in log_file:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("item") != item:
                        continue
                    entries.append(entry)
        except OSError:
            return []

        entries.sort(key=lambda entry: entry.get("timestamp") or "", reverse=True)
        limited = entries[:capped_limit]
        result: List[dict] = []
        for index, entry in enumerate(limited):
            normalized = dict(entry)
            normalized.setdefault("id", f"{normalized.get('timestamp', '')}-{index}")
            normalized["sequence"] = index + 1
            result.append(normalized)
        return result

    def get_no_permission(self):
        return Permission(canRead=False, canWrite=False, canAssign=False, canUnassign=False, canAssignAnyone=False)
    
    


    def get_sermon_permissions(self, user_id:str, item:str):
        sermon = self._sm.get_sermon_metadata(user_id, item)
        if not sermon:
            return self.get_no_permission()
        
        permissions = self._acl.get_user_permissions(user_id)        
        if not permissions:
            return self.get_no_permission()
        
        readPermissions = [p for p in permissions if p.find('read') >= 0]

        canRead = len(readPermissions) > 0

        writePermissions = [p for p in permissions if p.find('write') >= 0]
        if user_id == sermon.assigned_to:
            canWrite =  'write_owned_item' in writePermissions or 'write_any_item' in writePermissions
                
        else:
            canWrite =  'write_any_item' in writePermissions or 'assign_any_item' in permissions

        #admin can assign to anyone any item      
        #editor can assign to himself item that is not assigned
        #author can unassign item that's assigned to him, but can't unasign item that's assigned to others 
        # reader can't assign or unassign
        #assignment is not allowed if sermon is in development
        canAssign = False
        canUnassign = False
        canAssignAnyone = False
        canPublish = False
        canViewPublished = sermon.status == 'published' 
        if self._acl.get_status_order(sermon.status)  >  self._acl.get_status_order('in development'):
            canAssignAnyone = 'assign_any_item' in permissions
            if canAssignAnyone: #admin
                canAssign = False
                canUnassign = True
                canPublish = False
            elif 'assign_item' not in permissions: #reader
                canAssign = False
                canUnassign = False
                canPublish = False
            else: #editor
                if sermon.assigned_to:  
                    if user_id == sermon.assigned_to:
                        canUnassign = True
                        canAssign = False
                        canPublish =  self._acl.get_status_order(sermon.status) >= self._acl.get_status_order('ready')
                    else:
                        canUnassign = False
                        canAssign = False
                else:
                    canUnassign = False
                    canAssign = True    
        
        return Permission(canRead=canRead, canWrite=canWrite, canAssign=canAssign, canUnassign=canUnassign, 
                          canAssignAnyone=canAssignAnyone, canPublish=canPublish, canViewPublished=canViewPublished) 
    

    def assign(self, user_id:str, item:str, action:str) -> Permission: 
        sermon = self._sm.get_sermon_metadata(user_id, item) 
        if not sermon:
            return None
        permissions = self.get_sermon_permissions(user_id, item)
        context = None
        before_snapshot = None
        if action == 'assign' and permissions.canAssign:
            before_snapshot = self._snapshot_sermon_meta(sermon)
            sermon.assigned_to = user_id       
            sermon.assigned_to_name = self._acl.get_user(user_id).get('name')  
            sermon.status = 'assigned'  
            sermon.assigned_to_date = self._sm.convert_datetime_to_cst_string(datetime.now())
            context = 'assign'

        elif action == 'unassign' and permissions.canUnassign:
            before_snapshot = self._snapshot_sermon_meta(sermon)
            sermon.assigned_to = None
            sermon.assigned_to_name = None
            sermon.status = 'ready'
            sermon.assigned_to_date = None
            context = 'unassign'
        else:
            return None
        
        self._sm.save_sermon_metadata()
        if before_snapshot:
            self._log_sermon_meta_change(
                user_id,
                item,
                before_snapshot,
                self._snapshot_sermon_meta(sermon),
                context=context,
            )
        

        return self.get_sermon_permissions(user_id, item)
    
    def get_bookmark(self, user_id:str, item:str):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canRead:
            return  {"message": "You don't have permission to update this item"}
        
        return self._scm.get_bookmark(user_id, item)

    def set_bookmark(self, user_id:str, item:str, index:str):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canWrite:
            return  {"message": "You don't have permission to update this item"}
        
        self._scm.set_bookmark(user_id, item, index)
        return {"message": "bookmark has been set"}
    
    def get_users(self):
        return self._acl.users

    def get_user_info(self, user_id:str):   
        return self._acl.get_user(user_id)


    def publish(self, user_id:str, item:str):
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canPublish:
            return  {"message": "You don't have permission to publish this item"}
        
        sermon = self._sm.get_sermon_metadata(user_id, item)
        if not sermon:
            return {"message": "sermon not found"}
        before_snapshot = self._snapshot_sermon_meta(sermon)
        sermon.status = 'published'
        sermon.published_date = self._sm.convert_datetime_to_cst_string(datetime.now())
        self._sm.save_sermon_metadata()
        self._log_sermon_meta_change(
            user_id,
            item,
            before_snapshot,
            self._snapshot_sermon_meta(sermon),
            context="publish",
        )
        ScriptDelta(self.base_folder, item).publish(sermon.assigned_to)
        return {"message": "sermon has been published"}
    
    def get_final_sermon(self, user_id:str, item:str,  remove_tags:bool = True) -> dict:
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canRead:
            return  {"message": "You don't have permission to update this item"}

        sermon = self._sm.get_sermon_metadata(user_id, item)
        status = sermon.status
        sd = ScriptDelta(self.base_folder, item)
        sermon_data =  sd.get_final_script( status == 'published', remove_tags)
        sermon_data['metadata']['last_updated'] = sermon.published_date if status == 'published' else sermon.last_updated            
        sermon_data['metadata']['item'] = sermon.item
        sermon_data['metadata']['title'] = sermon.title if sermon.title else sermon.item
        sermon_data['metadata']['summary'] = sermon.summary
        sermon_data['metadata']['type'] = sermon.type
        sermon_data['metadata']['deliver_date'] = sermon.deliver_date
        sermon_data['metadata']['theme'] = sermon.theme if sermon.theme else sermon.title
        sermon_data['metadata']['assigned_to_name'] = sermon.assigned_to_name
        sermon_data['metadata']['author'] = sermon.author_name if sermon.author_name else '王守仁'
        sermon_data['metadata']['status'] = sermon.status
        sermon_data['metadata']['core_bible_verse'] = sermon.core_bible_verse
        sermon_data['metadata']['keypoints'] = sermon.keypoints if sermon.keypoints else ''
        sermon_data['metadata']['source'] = sermon.source 
        sermon_data['metadata']['series_id'] = self.get_series_id_for_item(item)

        return sermon_data
    
    def get_sitemap(self):
        return self._sm.get_sitemap()

    def search_script(self, item , text_list) -> dict:
        sd = ScriptDelta(self.base_folder, item)
        return sd.search(text_list)

    def search(self, term: str) -> dict:
        sd = ScriptDelta(self.base_folder, item)
        return sd.search([term])

    def chat( self, user_id : str, item :str, history:List[ChatMessage], is_published=False ) -> str:
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canRead:
            return  {"message": "You don't have permission to update this item"}
        
        sermon = self._sm.get_sermon_metadata(user_id, item)
        if not sermon:
            return  {"message": "sermon not found"}
        
        sd = ScriptDelta(self.base_folder, item)
        script = sd.get_final_script(is_published)
        article = sermon.title + '\n '
        if sermon.summary: 
            article +=  '簡介：' + sermon.summary + '\n'
        article +=  '\n'.join([ f"[{p['index']}] {p['text']}" for p in script['script'] ])
        copilot = Copilot()
        docs = [Document(item=item, document_content=article)]
        return copilot.chat(docs, history)

    def surmon_llm_chat(self, user_id: str, item: str, history: List[ChatMessage]) -> dict:
        permissions = self.get_sermon_permissions(user_id, item)
        if not permissions.canRead:
            return {"answer": "目前沒有權限檢視此講道，請確認登入狀態或請求權限。"}

        if not history:
            return {"answer": "請提供想詢問的問題。"}
        
        metadata = self._sm.get_sermon_metadata(user_id, item)
        if not metadata:
            return  {"answer": "sermon not found"}

        sd = ScriptDelta(self.base_folder, item)
        sermon_detail = sd.get_final_script(False)

        context_lines: List[str] = []
        total_length = 0
        max_length = 500000
        for paragraph in sermon_detail['script']:
            text = paragraph.get('text') or ""
            index = paragraph.get('index') or ""
            if not text:
                continue
            line = f"[{index}] {text.strip()}"
            context_lines.append(line)
            total_length += len(line)
            if total_length > max_length:
                context_lines.append("...（內容節錄）")
                break

        context = "\n".join(context_lines) if context_lines else "（暫無講道內容）"

        conversation_lines = []
        for message in history:
            role = "使用者" if message.role == 'user' else "助理"
            conversation_lines.append(f"{role}：{message.content.strip()}")

        conversation_text = "\n".join(conversation_lines)
        last_user_message = next((msg.content.strip() for msg in reversed(history) if msg.role == 'user'), "")

        surmon_title = metadata.title
#        theme = metadata.get('theme') or ""
#        deliver_date = metadata.get('deliver_date') or ""

        prompt = (
            "你是資深的基督教福音派牧師助理，請根據提供的講道內容回答問題，"
            "並保持語氣溫和、用詞貼近講員原意。必要時可引用講道段落索引"
            "（例如：[1] 或 [2_3]）。回答請使用繁體中文。\n\n"
            "若使用者的提問是聖經章節引用（例如：'約翰福音第四章十七到十八節'），請預設回傳該經文的和合本中文內容。\n\n"
            f"講道標題：{surmon_title}\n"
            "--- 講道段落（含索引） ---\n"
            f"{context}\n\n"
            "--- 對話歷史 ---\n"
            f"{conversation_text}\n\n"
            "請重點回答最後一個使用者的提問。請以講道內容為基礎進行回答。"
            "若問題超出講道直接提及的範圍，請依據講道的主題精神與上下文，"
            "結合你的聖經知識與牧養經驗提供合適的回答或延伸應用。\n"
            f"最新提問：{last_user_message}"
        )

        try:
            answer = gemini_client.generate(prompt,'gemini-2.5-flash').strip()
        except Exception as e:
            return {"answer": "抱歉，助理目前無法取得回應，請稍後再試。"}

        if not answer:
            answer = "抱歉，無法根據目前的講道內容提供回應。"

        return {"answer": answer}
    
    def summarize(self, title, items:List[str]) -> str:
        doc = ""
        for item in items:
            sd = ScriptDelta(self.base_folder, item)
            script = sd.get_final_script(False)
            article =  '\n\n'.join([ p['text'] for p in script['script'] ])
            doc += article + '\n\n'
        copilot = Copilot()
        history = [  ChatMessage(role='user',content='寫一段關於馬太福音 24 章簡介和講道的開場白') ]
        docs = [Document(item=title, document_content=doc)]
        return copilot.chat(docs, history)

    def save_sermon_series(self,  series_name:str, items:List[str]) -> str:
        """
        Save sermon series to the file.
        """
        doc = ""
        for item in items:
            sd = ScriptDelta(self.base_folder, item)
            script = sd.get_final_script(False)
            article =  '\n\n'.join([ p['text'] for p in script['script'] ])
            doc += article + '\n\n'
        with open(os.path.join(self.base_folder,'output', series_name +'.txt'), 'w') as f:
            f.write(doc)
        return doc

    def get_relevant_items(self, question)->set:
        url = f"{self.semantic_search_url}/semantic_search/{question}"
        response = requests.get(url)
        if response.status_code == 200:
            search_data = response.json()
            content_items = set([ d['content_id'] for d in search_data ])
            return content_items
        else:
            return set()


    def qa(self, user_id:str, history:List[ChatMessage]):        
        question = history[-1].content
        relevant_items = self.get_relevant_items(question)

        docs =[]
        for index, item in enumerate(relevant_items):
            meta = self._sm.get_sermon_metadata(user_id, item)        
            sd = ScriptDelta(self.base_folder, item)
            script = sd.get_final_script(meta.status == 'published')
            sermon = self._sm.get_sermon_metadata(user_id, item)
            article = sermon.title + '\n '
            if sermon.summary: 
                article +=  '簡介：' + sermon.summary + '\n'
                article +=  '\n'.join([ f"[{p['index']}] {p['text']}" for p in script['script'] ])
            docs.append(Document(item=item, document_content=article))
        copilot = Copilot()
        return copilot.chat(docs, history)
    
    def get_sermon_series(self):
        series_meta_file = os.path.join(self.config_folder, 'sermon_series.json')
        if not os.path.exists(series_meta_file):
            return []
        with open(series_meta_file, 'r', encoding='utf-8') as fsc:
            series_meta = json.load(fsc)
        for series in series_meta:
            sermons = []
            for item in series.get('sermons', []):
                sermon_meta = self._sm.get_sermon_metadata('junyang168@gmail.com', item)
                if sermon_meta:
                    sermons.append(sermon_meta)
            series['sermons'] = sermons
        return series_meta

    def _sanitize_series_id(self, series_id: str) -> str:
        normalized = (series_id or "").strip()
        normalized = normalized.replace(" ", "-")
        normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]", "-", normalized)
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        return normalized or "series"

    def _normalize_keypoints(self, raw_keypoints):
        if not raw_keypoints:
            return []
        entries: List[str] = []
        if isinstance(raw_keypoints, list):
            entries = [str(item).strip() for item in raw_keypoints if str(item).strip()]
        elif isinstance(raw_keypoints, str):
            lines = raw_keypoints.replace("\r", "").split("\n")
            for line in lines:
                cleaned = line.strip()
                cleaned = cleaned.lstrip("-•* \t")
                if cleaned:
                    entries.append(cleaned)
        return entries

    def _script_entries_to_markdown(self, entries: List[dict]) -> str:
        paragraphs: List[str] = []
        for entry in entries or []:
            text = ""
            index = None
            if isinstance(entry, dict):
                text = entry.get("text") or ""
                index = entry.get("index") or entry.get("start_index")
            else:
                text = getattr(entry, "text", "") or ""
                index = getattr(entry, "index", None) or getattr(entry, "start_index", None)
            text = text.strip()
            if not text:
                continue
            prefix = f"[{index}] " if index else ""
            paragraphs.append(f"{prefix}{text}")
        return "\n\n".join(paragraphs).strip()

    def _build_sermon_markdown_payload(self, sermon_payload: dict, fallback_title: str) -> str:
        metadata = sermon_payload.get("metadata", {}) if isinstance(sermon_payload, dict) else {}
        script_entries = sermon_payload.get("script") if isinstance(sermon_payload, dict) else None

        title = (metadata.get("title") or fallback_title or "").strip()
        summary = (metadata.get("summary") or "").strip()
        keypoints = self._normalize_keypoints(metadata.get("keypoints"))
        deliver_date = (metadata.get("deliver_date") or metadata.get("date") or "").strip()
        author = (metadata.get("author") or metadata.get("assigned_to_name") or "").strip()

        lines: List[str] = []
        if title:
            lines.append(f"# {title}")

        info_parts: List[str] = []
        if deliver_date:
            info_parts.append(f"日期：{deliver_date}")
        if author:
            info_parts.append(f"講員：{author}")
        if info_parts:
            lines.append("")
            lines.append("，".join(info_parts))

        if summary:
            lines.append("")
            lines.append("## 摘要")
            lines.append(summary)

        if keypoints:
            lines.append("")
            lines.append("## 講道要點")
            for kp in keypoints:
                lines.append(f"- {kp}")

        script_markdown = self._script_entries_to_markdown(script_entries or [])
        if script_markdown:
            lines.append("")
            lines.append("## 講道內容")
            lines.append(script_markdown)

        return "\n".join(lines).strip()

    def export_series_markdown(self, user_id: str, series_id: str) -> dict:
        if not user_id:
            raise PermissionError("需提供使用者帳號才能匯出 Markdown。")

        series_list = self.get_sermon_series()
        target_series = None
        for entry in series_list:
            if isinstance(entry, dict) and entry.get("id") == series_id:
                target_series = entry
                break
        if not target_series:
            raise ValueError(f"找不到系列：{series_id}")

        sermons = target_series.get("sermons") if isinstance(target_series, dict) else None
        if not sermons:
            raise ValueError(f"系列 {series_id} 尚未包含任何講道。")

        safe_series_id = self._sanitize_series_id(series_id)
        output_dir = Path(self.base_folder) / "series" / safe_series_id
        output_dir.mkdir(parents=True, exist_ok=True)

        generated_files: List[str] = []
        for sermon_entry in sermons:
            sermon_id = None
            sermon_title = series_id
            if isinstance(sermon_entry, dict):
                sermon_id = sermon_entry.get("item") or sermon_entry.get("id")
                sermon_title = sermon_entry.get("title") or sermon_entry.get("item") or series_id
            else:
                sermon_id = getattr(sermon_entry, "item", None)
                sermon_title = getattr(sermon_entry, "title", None) or sermon_id or series_id

            if not sermon_id:
                continue

            permissions = self.get_sermon_permissions(user_id, sermon_id)
            if not permissions.canRead:
                raise PermissionError(f"沒有權限讀取講道 {sermon_id}")

            sermon_payload = self.get_final_sermon(user_id, sermon_id, remove_tags=True)
            if not isinstance(sermon_payload, dict) or "script" not in sermon_payload:
                message = sermon_payload.get("message") if isinstance(sermon_payload, dict) else None
                raise ValueError(message or f"無法讀取講道 {sermon_id}")

            markdown_content = self._build_sermon_markdown_payload(sermon_payload, sermon_title)
            if not markdown_content:
                continue

            target_path = output_dir / f"{sermon_id}.md"
            target_path.write_text(markdown_content + "\n", encoding="utf-8")
            generated_files.append(str(target_path))

        return {
            "seriesId": series_id,
            "outputDir": str(output_dir),
            "sermonCount": len(generated_files),
            "generatedFiles": generated_files,
        }

    def get_article_series(self):
        articles_series = self.get_articles_and_series()
        series_meta = articles_series.get('series')

        new_series_meta = series_meta.copy()        
        for series in new_series_meta:
            series['articles'] = self.get_series_data(series, articles_series).get('articles', [])
        return new_series_meta
    
    def get_articles_and_series(self):
        series_meta_file = os.path.join(self.config_folder, 'article_series.json')
        if not os.path.exists(series_meta_file):
            return {"series": [], "articles": []}
        with open(series_meta_file, 'r', encoding='utf-8') as fsc:
            series_meta = json.load(fsc)
        return series_meta
    
    def get_series_data(self, series, articles_series):
        new_series = series.copy()
        new_series['articles'] = []
        for article in series.get('articles', []):
            article_meta = next((a for a in articles_series.get('articles', []) if a.get('item') == article), None)
            if article_meta:
                new_series['articles'].append(article_meta)
        return new_series

    def get_article_with_series(self, article_id:str):
        articles_series = self.get_articles_and_series()
        articles_meta = articles_series.get('articles', [])
        article_file = os.path.join(self.base_folder, 'article', article_id + '.md')
        with open(article_file, 'r', encoding='utf-8') as fsc:
            article_content = fsc.read()
        article_meta = next((a for a in articles_meta if a.get('item') == article_id), None)
        new_article = article_meta.copy()
        new_article['markdownContent'] = article_content
        new_article['series'] = []
        for series in articles_series.get('series', []):
            for aid in series.get('articles', []):
                if aid == article_id:
                    new_article['series'] = self.get_series_data(series, articles_series)
                    break
        return new_article

    def get_latest_articles(self, count:int = 2) -> List[dict]:
        articles = self.get_articles_and_series().get('articles', [])
        articles.sort(key=lambda x: x.get('deliver_date', ''), reverse=True)
        return articles[:count]
    
    def get_latest_sermons_articles(self, count:int = 2):
        sermons = self._sm.get_latest_sermons(count)
        articles = self.get_latest_articles(count)
        return {
            'sermons': sermons,
            'articles': articles
        }

    def quick_search(self, term: str) -> List[str]:
        now = datetime.utcnow()
        cached_entry = self._quick_search_cache.get(term)
        if cached_entry:
            cached_at, cached_ids = cached_entry
            if now - cached_at < self._quick_search_ttl:
                return cached_ids

        sermon_data = self._sm.get_sermon_meta_str()
        prompt = f"""The json data about a list of sermons. The id of sermons is "item" field.
Your task is return the most relevant sermons related to users's query using the json data. Sort by relevance in descending order.
return the list of sermon ids(item) only in json format.
sermon data:
{sermon_data}
User Query:
{term}
"""
        client = genai.Client() if env_file is None else genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        model = "gemini-2.5-flash"
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt),
                ],
            ),
        ]
        generate_content_config = types.GenerateContentConfig(
            thinking_config = types.ThinkingConfig(
                thinking_budget=0,
            ),
            response_mime_type="application/json",
        )

        response =  client.models.generate_content(
            model=model,
            contents=contents,
            config=generate_content_config,
        )
    #    print(response.usage_metadata)
        ids = json.loads(response.text)
        self._quick_search_cache[term] = (datetime.utcnow(), ids)
        return ids

class ConfigFileEventHandler(FileSystemEventHandler):
    def __init__(self, refreshers: list):
        self._refresheres = refreshers

    def on_modified(self, event):
        if event.is_directory:
            return
        for refresher in self._refresheres:
            if event.src_path.endswith(refresher[0]):
                refresher[1]()
                break



sermonManager = SermonManager()


if __name__ == '__main__':

    res = sermonManager.quick_search('因信稱義')

    article_with_no_series = sermonManager.get_article_with_series('解經法比較')
    series = sermonManager.get_article_series()
    article = sermonManager.get_article_with_series('馬太福音 24 章深入研讀 1')


    res = sermonManager.get_latest_sermons_articles(2)


    items = [
        '2021 NYSC 專題：馬太福音釋經（八）王守仁 教授 4之1',
        '2021 NYSC 專題：馬太福音釋經（八）王守仁 教授 4之2',
        '2021 NYSC 專題：馬太福音釋經（八）王守仁 教授 4之3',
        '2021 NYSC 專題：馬太福音釋經（八）王守仁 教授 4之4'   
    ]

    items = [
        '011WSR01',
        '011WSR02',
        '011WSR03',
        '2022年 NYSC 專題 馬太福音釋經（九）王守仁 教授  第二堂'
    ]
    resp = sermonManager.save_sermon_series('2022 NYSC 專題：馬太福音釋經（九）', items)
    print(resp)

    """
你是基督教福音派的資深基督徒。以下是週五團契的講稿。为以上讲稿生成 PPT。Slide 数量不要太多，中文用繁体。PPT 要包括讲稿中提到的圣经原文。
講稿：
    
    """


    resp = sermonManager.chat('junyang168@gmail.com', '2021 NYSC 專題：馬太福音釋經（八）王守仁 教授 4之1', [  ChatMessage(role='user',content='總結主题') ])
    print(resp)
    exit(0)
    

    sermons = sermonManager.get_sermons('junyang168@gmail.com')
    print(sermons)
    msg = sermonManager.get_sermon_detail('junyang168@gmail.com', '2019-2-15 心mp4', 'changes')
    print(msg)

    msg = sermonManager.get_sermon_detail('junyang168@gmail.com', '2019-2-15 心mp4', '')
    print(msg)


    permission = sermonManager.get_sermon_permissions('junyang168@gmail.com', '2019-2-15 心mp4')
    print('junyang', permission)
    permission = sermonManager.get_sermon_permissions('junyang168@gmail.com', '2019-4-4 神的國 ')
    print('junyang', permission)
    
    permission = sermonManager.get_sermon_permissions('jianwens@gmail.com', '2019-2-15 心mp4')
    print('jianwen', permission)
    permission = sermonManager.get_sermon_permissions('dallas.holy.logos@gmail.com', '2019-2-15 心mp4')
    print('admin', permission)
    
