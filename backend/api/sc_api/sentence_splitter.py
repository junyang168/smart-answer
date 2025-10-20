import difflib
import re

class SentenceSplitter:
    def __init__(self, timelineDictionary:dict):
        self.timelineDictionary = timelineDictionary

    def get_sentence_positions(self, text:str):
        positions = []
        for i, char in enumerate(text):
            if char in [ '.' , '?', '!', '。', '？','！']:
                positions.append(i)
        new_text = re.sub(r'[.,?!。？！，]', ' ', text)

        return new_text, positions

    def get_original_text_timeline(self, paragraph):
        start_timeline = self.timelineDictionary[paragraph['start_index'] if paragraph['start_index'] != '0' else '1_1']
        end_timeline = self.timelineDictionary[paragraph['index']]
        idx = 0
        org_text = ""
        sec_indx = {}
        while start_timeline != end_timeline:            
            org_text += start_timeline['text'] + ' '
            idx = len(org_text) 
            sec_indx[idx-1] = start_timeline
            start_timeline = self.timelineDictionary[ start_timeline['next_item'] ]
        org_text += start_timeline['text'] + ' '
        idx = len(org_text) 
        sec_indx[idx - 1] = start_timeline
        return org_text, sec_indx
    
    def calcuateTime(self, index, timestamp):
        sec = index.split('_')[0]
        ts = timestamp.split(',')[0].split(':')
        return (int(sec) - 1) * 60 * 20 + int(ts[0]) * 60 * 60 + int(ts[1]) * 60 + int(ts[2])


    def split_sentences(self, paragraph):
        org_text, sec_indx = self.get_original_text_timeline(paragraph)
        new_text, positions = self.get_sentence_positions(paragraph['text'])
        diff = difflib.Differ().compare(org_text, new_text)
        line = []
        org_idx = 0
        new_idx = 0
        match_pos = []
        for ele in diff:
            if ele.startswith('-'):
                org_idx += len(ele[2:])   
            elif ele.startswith('+'):
                new_idx += len(ele[2:])    
            else:
                match_pos.append((org_idx,new_idx))
                org_idx += 1
                new_idx += 1

        sentence_tl = []
        start_time = paragraph['start_time']
        for j, pos in enumerate(positions):
            m = next(( m for i, m in enumerate(match_pos) if 
                      m[1] == pos and 
                      match_pos[i-1][1] == pos-1 and
                      ( i+1 >= len(match_pos) or match_pos[i+1][1] == pos+1)                        
                      ), None)
            if m:
                org_sec = sec_indx[m[0]]
                sentence_tl.append({
                    'start_time': start_time,
                    'end_time': self.calcuateTime( org_sec['index'], org_sec['end_time']),
                    'text': paragraph['text'][ positions[j-1]+1 if j>0 else 0 : pos+1]
                })
                start_item = self.timelineDictionary[ org_sec['next_item'] ]
                start_time = self.calcuateTime(start_item['index'], start_item['start_time'])
        return sentence_tl  
    
    def get_duration(self, sentence):
        return sentence['end_time'] - sentence['start_time'] + 1
    
    def get_new_chunk(self):
        return { 'start_time': 0, 'end_time':0, 'text':'', 'sentences':[]}

    def split_chunks(self, paragraph):
        sentences = self.split_sentences(paragraph)
        chunks = []
        chunk = self.get_new_chunk()
        for sentence in sentences:
            if self.get_duration(chunk) + self.get_duration(sentence) > 30:
                if chunk['text']:
                    chunks.append(chunk)
                chunk = self.get_new_chunk()
            if not chunk['text']:
                chunk['start_time'] = sentence['start_time']

            chunk['end_time'] = sentence['end_time']
            chunk['text'] += sentence['text']
            chunk['sentences'].append(sentence)
        if chunk['text']:
            chunks.append(chunk)
        return chunks
