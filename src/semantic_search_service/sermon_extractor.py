import os
from langchain_core.prompts import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder
)
import sys
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)


from embed_content_extractor import embed_content_extractor
from langchain_anthropic import ChatAnthropic
from smart_answer_service.SA_config import configuration

class SermonExtractor(embed_content_extractor):    


    def get_script(self, script):
        return '\n\n'.join( [ f"[{i}]{p['text']}" for i, p in enumerate(script) ] )
    

    def _create_prompt(self,user_prompt_template :str):   
        messages = [HumanMessagePromptTemplate.from_template(user_prompt_template)]
        return ChatPromptTemplate.from_messages(messages)

    
    def split(self, text:str):

        template = """给下面基督教牧師講道加小标题，並返回起始段落的索引。{text}"""
        chat_prompt = self._create_prompt(template)

        cfg = configuration['holylogos']
        model_name = cfg.llm_config.model[len('anthropic/'):] 

        llm = ChatAnthropic(temperature=0,model_name= model_name)
        runnable = chat_prompt | llm
        out = runnable.invoke({'text':text})    
        return out.content
    


    def parse_ai_response(self, ai_response:str, script):
        lines = ai_response.split('\n')
        start = 0
        sections = []
        for l in lines:
            if not l or l.find('[') == -1:
                continue
            title = l[l.find(']')+1:]
            index = l[l.find('[')+1:l.find(']')]
            arr = index.split('-')
            if len(arr) == 1:
                idx_start = int(index)
                idx_end = idx_start
            else:
                idx_start = int(arr[0])
                idx_end = int(arr[1])
            section = title + '\n' + '\n\n'.join( [ p['text'] for p in script[idx_start:idx_end+1] ] )
            sections.append(section)

        return sections
    
    def get_source(self)->str:
        return "holylogos"
    
    def __init__(self) -> None:
        self.base_dir = os.getenv('base_dir')
    
    def get_content(self, meta:dict, script) -> list[str]:

        item_id = meta.get('item')        
        file_path =  os.path.join(self.base_dir, 'content_store', item_id + '.json')

        if os.path.isfile(file_path):
            with open(item_id, 'r') as file:
                sections = json.load(file)
        else:
            text = self.get_script(script)
            ai_response = self.split(text)
            sections = self.parse_ai_response(ai_response, script)
            with open(file_path, 'w', encoding='UTF-8') as file:
                json.dump(sections, file, ensure_ascii=False, indent=4)


        return sections


    def get_metadata(self, meta:dict, content:dict) -> dict:
        return meta


if __name__ == "__main__":
    se = SermonExtractor()

    ai_response = '好的,我根據您的講道內容,為每個段落加上小標題如下:\n\n[0] 教會裡面的傳統教導\n[1] 保羅處理哥林多教會關於吃祭偶像之物的問題\n[2] 要從聖經來看保羅的教導\n[3-4] 保羅在哥林多前書的教導(一)\n[5] 保羅在哥林多前書的教導(二) \n[6-7] 解釋"良心軟弱就污穢了"的意思\n[8-10] 保羅關於在市場上買祭偶像之物的教導\n[11-14] 總結保羅關於吃祭偶像之物的教導\n[15-20] 一個弟兄因不明白聖經教導而受虧損的例子\n[21-22] 再次總結保羅關於吃祭偶像之物的教導\n[23-29] 使徒行傳15章的背景\n[30-32] 雅各在使徒行傳15章的提議\n[33-34] 使徒們寫信吩咐外邦信徒的四點要求\n[35-38] 使徒們寫信的對象:安提阿、敘利亞、基利家的外邦信徒\n[39-42] 從利未記看神對猶太人的特別要求\n[43-44] 再次強調使徒們信的對象和原因\n[45-47] 使徒行傳15章和哥林多前書的教導對比\n[48-52] 聖經原則在不同處境中的應用\n[53-58] 如何從聖經細則中找出聖經原則\n[59-64] 女人蒙頭的聖經原則和今日應用\n[65-69] 哥林多前書8章的教導\n[70-73] 不叫人跌倒的重要性\n[74-79] 約翰一書3章的教導\n[80-81] 哥林多前書10章的教導\n[82-83] 保羅憑良心行事為人\n[84] 保羅勸勉提摩太要存無虧的良心\n[85-87] 良心的定義和功用\n[88-90] 要使良心的標準與聖經的標準一致\n[91] 呼籲弟兄姐妹明白聖經真理,使用良心來生活\n\n講道的起始段落是[0]。'
    sections = se.parse_ai_response(ai_response, se.sermons[0]['script'])
    print(sections)
    pass

#    se.get_embedded_content()
    
