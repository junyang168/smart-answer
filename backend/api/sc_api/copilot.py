import json
import os
#from openai import OpenAI
from pydantic import BaseModel
from typing import List
from .image_to_text import ImageToText
import xml.etree.ElementTree as ET
from google import genai
from google.genai import types

from dotenv import load_dotenv
env_file = os.getenv("ENV_FILE")
print(f'env_file: {env_file}')
if env_file:
    load_dotenv(env_file)
else:
    load_dotenv()  # Fallback to default .env file in the current directory



class ChatMessage(BaseModel):
    role: str
    content: str

from pydantic import BaseModel
from typing import List,Optional

class Reference(BaseModel):
    Id : str
    Title: Optional[str] = None
    Link : Optional[str] = None
    Index : str

class ChatResponse(BaseModel):
    quotes: List[Reference]
    answer: Optional[str] = None


class Document(BaseModel):
    item: str
    document_content: str
    



class Copilot:
    def map_prompt(self, question:str) -> str:
        if question == "總結主题":
            return """总结下面牧师讲道的主題和主要观点.回答以下格式。例子：
        主题
        1. 观点 1
        2. 观点 2"""   
        else:
            return question
    


    def parse_quotes(self, quotes) -> List[Reference]:
        if quotes is None:
            return []
        results = []
        for q in quotes:
            for child in q:
                if child.tag == 'para_index':
                    index = child.text
                elif child.tag == 'text':
                    text = child.text
            results.append( Reference(Id=q.attrib['index'], Title=text, Link=q.attrib['source'], Index=index) )
        return results


    def parse_response(self, response:str) -> dict:
        return ChatResponse(quotes=[], answer=response )
        root = ET.fromstring("<root>" + response + "</root>")
        quotes = root.findall('.//quotes/quote')
        quotes = self.parse_quotes(quotes)
        answer_node = root.find('.//answer')
        return ChatResponse(quotes=quotes, answer=answer_node.text if answer_node is not None else None)
    
    def get_context(self, docs:List[Document]) -> str:
        context_str = ""
        for index, doc in enumerate(docs):
            context_str += f"""<document index='{index+1}'>
                <source>/article?i={doc.item}</source>
                <document_content>{doc.document_content}</document_content>
            </document>"""
        return context_str

        
    def chat(self, docs: List[Document] , history: List[ChatMessage]) -> str:
#        quotes=[Reference(Id='1', Title='保羅教導吃祭偶像之物若叫人跌倒就不可吃，若不叫人跌倒就可吃，核心原則是「不叫人跌倒」[2_232]。', Link='/article?i=2019-2-18 良心', Index='2_232'), 
#                Reference(Id='2', Title='聖經原則需根據處境應用，若活動涉及與鬼相交或違背真理，應禁戒[1_372]。', Link='/article?i=2019-2-18 良心', Index='1_372'), 
#                Reference(Id='3', Title='使徒行傳15章禁止外邦信徒參與祭偶像之事，因會絆倒猶太人，強調行為須合乎真理與愛心[2_211]。', Link='/article?i=2019-2-18 良心', Index='2_211')] 
#        answer='\n基督徒是否慶祝萬聖節需根據聖經原則審慎判斷。首先，若節日活動涉及與邪靈相關的儀式（如祭鬼），聖經明示不可參與（2）。其次，即使某些活動表面無害，若可能使軟弱的弟兄絆倒或誤解信仰，應出於愛心放棄自由（1）。此外，良心的標準需以聖經真理校對，而非僅憑主觀平安感（3）。因此，若慶祝方式包含違背真理的元素，或影響他人對信仰的認知，信徒應選擇禁戒，以榮耀神並造就人為優先[1][2][3]。'
#        return ChatResponse(quotes=quotes, answer=answer)

        question = history[-1].content
        prefix = "提取文字 at "
        if question.startswith(prefix):
            i2t = ImageToText(docs[0].item)
            timestamp = int(question[len(prefix):])
            res = i2t.extract_slide(timestamp)
            return ChatResponse(quotes=[], answer=res)
        else:
            history[-1].content = self.map_prompt( question )

            context_str = self.get_context(docs)
            
            messages = [
                {
                    "role": "system", 
                    "content":self.system_prompt.format(context_str=context_str)
                }  
            ]
            # Add the history messages ignoring text extraction
            i = 0
            while i < len(history):
                msg = history[i]
                if msg.role =='user' and msg.content.startswith(prefix) :
                    i += 1
                else:
                    if msg.role in [ 'user','assistant' ]:
                        messages.append({"role": msg.role , "content": msg.content})
                i += 1
            
#            res = '<quotes>\n    <quote index=\'1\' source=\'/article?i=2019-05-19 喜乐\'>\n        <text>良心需以圣经真理为校准标准，而非单纯依赖主观感受</text>\n        <para_index>1_10-1_24</para_index>\n    </quote>\n    <quote index=\'2\' source=\'/article?i=2019-05-19 喜乐\'>\n        <text>基督徒行为的核心原则是"不叫人跌倒"</text>\n        <para_index>1_193-1_227</para_index>\n    </quote>\n    <quote index=\'3\' source=\'/article?i=2019-05-19 喜乐\'>\n        <text>良心的衡量标准需要与圣经真理校对方可靠</text>\n        <para_index>3_398-3_413</para_index>\n    </quote>\n</quotes>\n<answer>\n主题：基督徒良心的正确运用与圣经真理的关系\n\n1. 传统教会教导"凭良心平安行事"的观点与圣经真理相冲突，良心必须以圣经真理为校准标准，而非单纯依赖主观感受[1]。\n2. 基督徒行为的核心原则是"不叫人跌倒"，这体现在处理吃祭偶像之物等具体问题上需要根据圣经原则灵活应用[2]。\n3. 良心的作用包括衡量能力（神所赐）和衡量标准（受文化教育影响），必须通过深度解经使良心标准与圣经真理一致才能可靠运用[3]。\n</answer>'
#            return self.parse_response(res)
            provider = os.getenv("PROVIDER")
            model = os.getenv("MODEL")
#            if provider == 'gemini':
            res = self.call_gemini(messages, model)
            return  self.parse_response(res) 
    
    def call_gemini(self, messages: List[dict], model: str = "gemini-1.5-flash") -> str:
        system_message = messages[0]
        config=types.GenerateContentConfig(
                system_instruction=system_message['content'])

        user_message = messages[-1]
        messages = messages[1:-1]  # Exclude the last user message
        print(f'messages: {messages}')
        historyontent = [ { 'role':  message['role'] if message['role'] == 'user' else 'model', 'parts': [{'text':message['content']}]} for message in messages]
        print(f'historyontent: {historyontent}')
        client = genai.Client() if env_file is None else genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        chat = client.chats.create(model=model,history=historyontent)
        resp =  chat.send_message(user_message['content'],config=config)
        return resp.text
    


    def __init__(self):
        
        self.system_prompt = """你是資深的基督教福音派牧師。現在要回答與下面講道相關的問題。回答符合講道的聖經觀點。
1. 下面提供了幾篇相關的講道。每篇講道以<document>標籤分隔。講道的內容放在<document><document_content>標籤中. 講道的每個段落前都有一個索引號碼。（例如[1]或[1_1])。講道的來源放在<document><source>標籤中. 講道的索引號碼放在<document>的index屬性中.
2. 回答問題時，應將答案放在<answer></answer>標籤中。不要直接引用或逐字重複引文內容。回答時，不要說“根據Quote 1”。若答案中某部分與特定引文相關，在回答每個相關部分的句子末尾，僅透過添加帶括號的數字來引用相關的引文。（例如：這是一個示例句子[1]。）
3. 從講道中找出與回答問題最相關的引文。將引文按編號順序放在<quotes></quotes> 的<text></text>標籤中，引文應相對簡短。講道文章source放在<quote>的<source>標籤中. 
4. 引用段落的索引號碼應放在<quote>的<para_index>標籤中。索引號碼應該是講道的段落索引號碼，這些索引號碼應該與講道內容中的段落索引號碼相對應。
5. The format of your overall response should look like what’s shown between the <examples></examples> tags. Make sure to follow the formatting and spacing exactly. If the question cannot be answered by the document, say so.
<examples>
	<quotes>
		<quote index='1' source='http://localhost:8000/public/2019-05-19 喜乐'>
			<text>保羅說，A.如果你吃祭偶像的東西，參與祭偶像，就不可以吃。</text>
			<para_index>1_1-1_3</para_index>
	</quote>
	<quote index='2 source='http://localhost:8000/public/2019-05-19 喜乐'>
		<text>所以保羅說你們只管吃，不要為良心的緣故問什麼話</text>
		<para_index>2_1-2_3</para_index>
	</quote>
</quotes>
<answer>
</answer>
</examples>
讲道内容:
{context_str}


请开始对话："""

        self.system_prompt = """
你是資深的基督教福音派牧師。現在要回答與下面講道相關的問題。回答符合講道的聖經觀點。
 下面提供了幾篇相關的講道。每篇講道以<document>標籤分隔。講道的內容放在<document><document_content>標籤中. 講道的每個段落前都有一個索引號碼。（例如[1]或[1_1])。講道的來源放在<document><source>標籤中. 講道的索引號碼放在<document>的index屬性中.
讲道内容:
{context_str}
"""


if __name__ == "__main__":
    copilot = Copilot()

    ans = copilot.chat('2019-05-19 喜乐', '2019-05-19 喜乐', [ChatMessage(role='user', content='總結主题')])
    print(ans)
