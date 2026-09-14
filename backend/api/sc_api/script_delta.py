import json
import difflib
import jsonlines
import os
import math
import re
import datetime
import tempfile
import hashlib
import fcntl
import warnings
from pathlib import Path

from .sentence_splitter import SentenceSplitter


class ScriptConflictError(RuntimeError):
    """The editor tried to save over a newer script snapshot."""


class ScriptDelta:

    def __init__(self, base_folder:str, item_name:str) -> None:
        self.base_folder = base_folder
        self.item_name = item_name
        self.timelineDictionary = self.loadTimeline()



    def loadPatchedScript(self):
        with open( self.base_folder +  '/script_patched/' + self.item_name + '.json', 'r') as file1:
            self.patched = json.load(file1)
            return [ p.get('text') for p in  self.patched ]

    def loadReviewScript(self):
        with open( self.base_folder +  '/script_review/' + self.item_name + '.json', 'r') as file1:
            self.review = json.load(file1)
            return [ p.get('text') for p in  self.review ]

    def loadPublishedScript(self):
        with open( self.base_folder +  '/script_published/' + self.item_name + '.json', 'r') as file1:
            published_data = json.load(file1)
            return published_data['metadata'],  [ {'text': p.get('text')} for p in  published_data['script'] ]


    def loadTimeline(self):
        with open(self.base_folder +  '/script/' + self.item_name + '.json', 'r') as f:           
            timeData = json.load(f) 

            timelineData = timeData['entries']  
            for i in range(1, len(timelineData)):
                timelineData[i-1]['next_item'] = timelineData[i]['index'] if i < len(timelineData) else None

            return  { t['index'] : t  for t in  timelineData }
            

    def get_end_tag(self, tag:str):
        if len(tag) > 0:
            return '</>'
        else:
            return ''
    
    def set_tag(self, current_tag, line, new_tag):
        if  current_tag != new_tag:
            line.append(self.get_end_tag(current_tag))
            line.append(new_tag)
        return new_tag
    

    def calcuateTime(self,index, timestamp):
        if str(index).find('_') >= 0:
            sec = index.split('_')[0]
        else:
            sec = 1
        ts = timestamp.split(',')[0].split(':')
        return (int(sec) - 1) * 60 * 20 + int(ts[0]) * 60 * 60 + int(ts[1]) * 60 + int(ts[2])
        


    def formatTime(self, time):
        h = time // 3600
        m = (time % 3600) // 60
        s = time % 60
        return f'{h:02}:{m:02}:{s:02}'

    def add_timeline(self, paragraphs):
        paragraphs = [ p for p in paragraphs if p.get('type') != 'comment']
        for i in range(0, len(paragraphs)):
            para = paragraphs[i]
            if isinstance(para.get('index'), str) and para['index'].startswith('subtitle-'):
                continue

            if para.get('end_index'):
                start_item = self.timelineDictionary[  para['index'] ] 
                if start_item:
                    para['start_index'] = start_item['index']
                    para['start_time'] = self.calcuateTime(start_item['index'], start_item['start'])
                    para['start_timeline'] = self.formatTime(para['start_time'])
                else:
                    para['start_timeline'] = '00:00:00'
                    para['start_index'] = '0'
                    para['start_time'] = 0                    

                this_timeline = self.timelineDictionary[ para['end_index'] ]
                if this_timeline:
                    para['end_time'] = self.calcuateTime(this_timeline['index'], this_timeline['end'])
                else:
                    para['end_time'] = 9999999999
            else:
                timelineItem = self.timelineDictionary[  paragraphs[i-1]['index'] ] if i > 0 else None
                if timelineItem:
                    start_item =  self.timelineDictionary[timelineItem['next_item']]
                    para['start_index'] = start_item['index']
                    para['start_time'] = self.calcuateTime( start_item['index'] , start_item['start'])
                    para['start_timeline'] = self.formatTime(para['start_time'])
                else:
                    para['start_timeline'] = '00:00:00'
                    para['start_index'] = '0'
                    para['start_time'] = 0

                this_timeline = self.timelineDictionary[ para['index'] ]
                if this_timeline: 
                    para['end_time'] = self.calcuateTime( this_timeline['index'], this_timeline['end']);
                else:
                    para['end_time'] = 9999999999

    def create_paragraph(self, paragraph, text):
        new_paragraph = paragraph.copy()
        new_paragraph['text'] = text
        return new_paragraph
    


    def get_script(self, with_chanages:bool):
        if with_chanages:
            return self.getChanges()
        else:
            with open( self.base_folder +  '/script_review/' + self.item_name + '.json', 'r') as file1:
                self.patched = json.load(file1)
            self.add_timeline(self.patched)
            return self.patched
        
        

    def compare_text(self, patched_i: int , review_i:int):
        p1 = self.patched[patched_i]
        p2 = self.review[review_i]
        if p2.get('type') == 'comment': 
            return False
        
        ratio = difflib.SequenceMatcher(None, p1['text'], p2['text']).ratio() 
        if ratio > 0.8:        
            return True
        return  p1['index'] == p2['index']



    def getChanges(self):

        paragraphs_patched = self.loadPatchedScript()
        paragraphs_review = self.loadReviewScript()
        self.add_timeline(self.patched)

        iPatched = 0
        iReview = 0
        len_patched = len(paragraphs_patched)
        len_review = len(paragraphs_review)
        script_changes = []
        while iPatched < len_patched and iReview < len_review:    
            while iPatched < len_patched and iReview < len_review and  self.compare_text(iPatched, iReview):
                diff = difflib.Differ().compare(paragraphs_patched[iPatched], paragraphs_review[iReview])
                line = []
                change_tag = ''
                for ele in diff:
                    if ele.startswith('-'):
                        change_tag = self.set_tag(change_tag, line,'<->')
                        line.append( ele.replace('-','').strip() )  
                    elif ele.startswith('+'):
                        change_tag = self.set_tag(change_tag, line,'<+>')
                        line.append( ele.replace('+','').strip() )  
                    else:
                        change_tag = self.set_tag(change_tag, line,'')
                        line.append( ele.strip() )  
                para = self.create_paragraph(self.patched[iPatched], ''.join(line) ) 
                script_changes.append(para)
                iPatched += 1
                iReview += 1

            j = iReview + 1        
            #difflib.SequenceMatcher(None, paragraphs_patched[iPatched], paragraphs_review[j]).ratio() < 0.8
            while iPatched < len_patched and j < len_review and not self.compare_text(iPatched, j) :
                j += 1
            
            if j >= len_review:
                if iPatched < len_patched:
                    p = self.create_paragraph(self.patched[iPatched], '<->' + self.patched[iPatched]['text'] + '</>')
                    script_changes.append(p)
                iPatched += 1
            else:
                for k in range(iReview, j):
                    p = self.create_paragraph(self.patched[iPatched-1],  '<+>' + paragraphs_review[k] + '</>' )
                    script_changes.append(p)
                iReview = j
        

        for k in range(iPatched, len_patched):
            p = self.patched[k]
            p['text'] = '<->' + p['text'] + '</>'
            script_changes.append(p)

        for k in range(iReview, len_review):
            p = { 'text': '<+>' + paragraphs_review[k] + '</>', 'index': self.patched[iPatched-1].get('index') }
            script_changes.append(p)

        return script_changes
    
  
    def save_script(
        self,
        user_id: str,
        type: str,
        item: str,
        data,
        *,
        expected_current_sha256: str | None = None,
    ):
        if type not in {'scripts', 'slides'}:
            raise ValueError(f"unsupported update type: {type}")
        if type == 'scripts' and not expected_current_sha256:
            raise ValueError("expected_current_sha256 is required for script updates")
        folder = 'slide' if type == 'slides' else 'script_review'
        data_dicts = []
        for p in data:
            ent = { 'text':p.text }
            if p.type:
                ent['type'] = p.type
                ent['user_id'] = p.user_id
            if p.index:
                ent['index'] = p.index
            if p.end_index:
                ent['end_index'] = p.end_index
            data_dicts.append(ent)
        written_sha256 = self.save_script_rows(
            folder,
            data_dicts,
            expected_current_sha256=expected_current_sha256,
        )

        return {
            "message": f"{folder} updated successfully",
            "script_sha256": written_sha256,
        }

    def save_script_rows(
        self,
        folder: str,
        rows,
        *,
        expected_current_sha256: str | None = None,
    ):
        """Atomically save already-normalized rows without dropping their fields.

        The editor's ``save_script`` deliberately projects Pydantic objects to
        its historical wire shape. Pipeline subtitle persistence starts with
        the canonical JSON rows and must prove every existing row stayed exact,
        so it uses this narrower mapping-based entry point.
        """

        return self.save_rows(
            self.base_folder,
            self.item_name,
            folder,
            rows,
            expected_current_sha256=expected_current_sha256,
        )

    @staticmethod
    def read_rows_with_sha(base_folder: str, item_name: str, folder: str):
        """Read one exact script snapshot under the same lock used by writers."""

        if folder not in ('slide', 'script_review'):
            raise ValueError(f"unsupported script folder: {folder}")
        target = os.path.join(base_folder, folder, item_name + '.json')
        lock_path = os.path.join(
            os.path.dirname(target), f".{os.path.basename(target)}.lock"
        )
        with open(lock_path, "a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
            raw = Path(target).read_bytes()
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError(f"{target}: script must be a JSON array")
        return rows, hashlib.sha256(raw).hexdigest()

    def get_review_script_with_sha(self):
        rows, source_sha256 = self.read_rows_with_sha(
            self.base_folder, self.item_name, "script_review"
        )
        self.patched = rows
        self.add_timeline(self.patched)
        return self.patched, source_sha256

    @staticmethod
    def save_rows(
        base_folder: str,
        item_name: str,
        folder: str,
        rows,
        *,
        expected_current_sha256: str | None = None,
    ):
        """Atomically save mappings, with an optional compare-and-swap guard.

        Every writer takes the same sidecar lock. Pipeline writes additionally
        supply the SHA they read, so an editor save that lands while headings
        are being generated makes the pipeline fail instead of being
        overwritten by a stale result.
        """

        if folder not in ('slide', 'script_review'):
            raise ValueError(f"unsupported script folder: {folder}")
        if folder == 'script_review' and not expected_current_sha256:
            raise ValueError(
                "expected_current_sha256 is required for script_review writes"
            )
        target = os.path.join(base_folder, folder, item_name + '.json')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        lock_path = os.path.join(
            os.path.dirname(target), f".{os.path.basename(target)}.lock"
        )
        with open(lock_path, "a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if expected_current_sha256 is not None:
                actual = (
                    hashlib.sha256(Path(target).read_bytes()).hexdigest()
                    if os.path.exists(target)
                    else None
                )
                if actual != expected_current_sha256:
                    raise ScriptConflictError(
                        "script changed before atomic save: "
                        f"expected {expected_current_sha256}, found {actual or 'missing'}"
                    )
            target_mode = os.stat(target).st_mode & 0o777 if os.path.exists(target) else 0o644
            encoded = json.dumps(list(rows), ensure_ascii=False, indent=4).encode("UTF-8")
            written_sha256 = hashlib.sha256(encoded).hexdigest()
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{item_name}.", suffix=".tmp", dir=os.path.dirname(target)
            )
            try:
                os.chmod(temporary, target_mode)
                with os.fdopen(descriptor, "wb") as file:
                    file.write(encoded)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, target)
                # Verify the installed bytes before releasing the lock.  A
                # caller-side read would race with the next valid writer and
                # could falsely report this successful commit as a failure.
                committed_sha256 = hashlib.sha256(Path(target).read_bytes()).hexdigest()
                if committed_sha256 != written_sha256:
                    raise RuntimeError("installed script bytes differ from authorized payload")
                directory_fd = os.open(os.path.dirname(target), os.O_RDONLY)
                try:
                    try:
                        os.fsync(directory_fd)
                    except OSError as exc:
                        # The file contents were already fsynced and atomically
                        # installed. Reporting the operation as failed here
                        # invites a retry that inserts the same subtitles again.
                        warnings.warn(
                            f"could not fsync script directory after committed save: {exc}",
                            RuntimeWarning,
                        )
                finally:
                    os.close(directory_fd)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
            return written_sha256


    @staticmethod
    def remove_format(text:str):
        text =  re.sub(r'~~(.|\n)*?~~', '', text) 
        idx = text.find('~~')
        if idx >= 0 :
            text = text[:idx]       
        return re.sub(r'\*(.*?)\*', r'\1', text).strip()
    
    
    def get_clean_script(self, paras):
        for p in paras:
            if p.get('text'):
                p['text'] = ScriptDelta.remove_format(p.get('text'))
        for i in reversed(range(1, len(paras))):
            if not paras[i]['text'] :
                paras.pop(i)
        return paras
    
    def get_final_script_data(self):
        self.loadReviewScript()
        self.add_timeline(self.review)
        return self.review

    def get_final_script(self, is_published:bool=True, remove_tags:bool=True):
        if is_published:
            metadata, sermon_detail = self.loadPublishedScript()
        else:
            self.loadReviewScript()
            sermon_detail = self.review
            metadata = {}
        
        if remove_tags:
            sermon_detail = self.get_clean_script(sermon_detail)
        return { 'metadata': metadata, 'script': sermon_detail}


    def publish(
        self,
        author: str,
        *,
        expected_review_sha256: str | None = None,
        expected_published_sha256: str | None = None,
    ) -> str:
        """Atomically publish one exact review snapshot.

        Holding the review lock through the published-file replacement prevents
        a source correction from claiming success after a concurrent editor has
        changed the review transcript. The optional SHA is mandatory for
        pipeline/governed repairs; the interactive legacy caller still
        publishes whichever complete snapshot it locks.
        """

        review_target = os.path.join(
            self.base_folder, "script_review", self.item_name + ".json"
        )
        review_lock_path = os.path.join(
            os.path.dirname(review_target), f".{os.path.basename(review_target)}.lock"
        )
        published_target = os.path.join(
            self.base_folder, "script_published", self.item_name + ".json"
        )
        os.makedirs(os.path.dirname(published_target), exist_ok=True)
        published_lock_path = os.path.join(
            os.path.dirname(published_target),
            f".{os.path.basename(published_target)}.lock",
        )

        with open(review_lock_path, "a+b") as review_lock:
            fcntl.flock(review_lock.fileno(), fcntl.LOCK_SH)
            review_raw = Path(review_target).read_bytes()
            review_sha256 = hashlib.sha256(review_raw).hexdigest()
            if (
                expected_review_sha256 is not None
                and review_sha256 != expected_review_sha256
            ):
                raise ScriptConflictError(
                    "review script changed before publish: "
                    f"expected {expected_review_sha256}, found {review_sha256}"
                )
            published_script = json.loads(review_raw)
            if not isinstance(published_script, list):
                raise ValueError(f"{review_target}: review script must be a JSON array")
            self.add_timeline(published_script)
            published = {
                "metadata": {
                    "author": author,
                    "title": self.item_name,
                    "status": "published",
                    "last_updated": datetime.datetime.now(
                        datetime.timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                "script": published_script,
            }
            encoded = json.dumps(
                published, ensure_ascii=False, indent=4
            ).encode("UTF-8")
            written_sha256 = hashlib.sha256(encoded).hexdigest()

            with open(published_lock_path, "a+b") as published_lock:
                fcntl.flock(published_lock.fileno(), fcntl.LOCK_EX)
                if expected_published_sha256 is not None:
                    actual_published_sha256 = (
                        hashlib.sha256(Path(published_target).read_bytes()).hexdigest()
                        if os.path.exists(published_target)
                        else None
                    )
                    if actual_published_sha256 != expected_published_sha256:
                        raise ScriptConflictError(
                            "published script changed before publish: "
                            f"expected {expected_published_sha256}, "
                            f"found {actual_published_sha256 or 'missing'}"
                        )
                target_mode = (
                    os.stat(published_target).st_mode & 0o777
                    if os.path.exists(published_target)
                    else 0o644
                )
                descriptor, temporary = tempfile.mkstemp(
                    prefix=f".{self.item_name}.",
                    suffix=".tmp",
                    dir=os.path.dirname(published_target),
                )
                try:
                    os.chmod(temporary, target_mode)
                    with os.fdopen(descriptor, "wb") as file:
                        file.write(encoded)
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(temporary, published_target)
                    installed = hashlib.sha256(
                        Path(published_target).read_bytes()
                    ).hexdigest()
                    if installed != written_sha256:
                        raise RuntimeError(
                            "installed published bytes differ from authorized payload"
                        )
                    directory_fd = os.open(
                        os.path.dirname(published_target), os.O_RDONLY
                    )
                    try:
                        try:
                            os.fsync(directory_fd)
                        except OSError as exc:
                            warnings.warn(
                                "could not fsync published-script directory after "
                                f"committed save: {exc}",
                                RuntimeWarning,
                            )
                    finally:
                        os.close(directory_fd)
                except Exception:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
                    raise
        return written_sha256




    def search(self, text_list:list[str]):
        published_script = self.get_final_script(True)
        match_result = {}
        for text in text_list:
            text2 = text + '~'
            for p in published_script['script']:
                if not p['text']:
                    continue 
                diff = difflib.Differ().compare(p['text'], text2)
                line = []
                for ele in diff:
                    if ele.startswith('-'):
                        pass
                    elif ele.startswith('+'):
                        ch = ele.strip()
                        if ch.find('~') >= 0:
                            break
                        line.append( ch.replace(' ','') )  
                    else:
                        line.append( ele.strip() )
                changed_text = ''.join(line)  
                if difflib.SequenceMatcher(None, text, changed_text).ratio() >= 0.9:
                    match_result[text] =  p['start_index'] 
                    break

        return match_result 

    def get_chunks(self):
        splitter = SentenceSplitter(self.timelineDictionary)
        script = self.get_final_script(is_published=True, remove_tags=False)
        chunks = []
        for para in script['script']:
            if para['text'].find('~~') >= 0:
                continue
            para['text'] = re.sub(r'\*(.*?)\*', r'\1', para['text']).strip()
            splitted_chunks = splitter.split_chunks(para)
            for c in splitted_chunks:
                c['para_start_index'] = para['start_index'] 
                c['para_end_index'] = para['index'] 
            chunks.extend( splitted_chunks )
        return chunks






if __name__ == '__main__':
    delta = ScriptDelta('/Users/junyang/church/data', '2019-2-18 良心')
    chunks = delta.get_chunks()
    print(chunks)
#    delta.get_final_script_data()
#    splitter = SentenceSplitter(delta.timelineDictionary)   
#    splitter.split_chunks(delta.review[1])

#    delta = ScriptDelta('/Users/junyang/church/data', '2019-05-26 罗马书5章1至2节')
#    script =  delta.get_final_script(False)
#    delta.publish('junyang168@gmail.com')
#    res = delta.search(['論到祭偶像之物,我們曉得偶像在世上算不得甚麼,也知道神只有一位,再沒有別的神。'])
#    print(res)
#    text = delta.get_script_text(with_index=True)
#    delta.publish('junyang168@gmail.com')
