import os
from openai import OpenAI

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

    def markdown_to_json_to_dict(self, markdown: str) -> dict:
        """Convert markdown-formatted JSON string to Python dictionary.
        
        Args:
            markdown: String containing JSON wrapped in markdown code block
            
        Returns:
            Parsed dictionary from JSON content
        """
        import json
        import re
        
        # Extract JSON content between markdown code blocks
        match = re.search(r'```json\s*({.*?})\s*```', markdown, re.DOTALL)
        if not match:
            raise ValueError("No valid JSON markdown block found")
            
        return json.loads(match.group(1))

    def get_script(self, script):
        return '\n'.join( [ f"{p['text']}" for  p in script ] )
    

    def _create_prompt(self,user_prompt_template :str):   
        messages = [HumanMessagePromptTemplate.from_template(user_prompt_template)]
        return ChatPromptTemplate.from_messages(messages)

    
    def split(self, text:str):

#        template = """给下面基督教牧師講道加小标题，並返回起始段落的索引。{text}"""
#        chat_prompt = self._create_prompt(template)

#        cfg = configuration['holylogos']
#        model_name = cfg.llm_config.model[len('anthropic/'):] 

#        llm = ChatAnthropic(temperature=0,model_name= model_name)
#        runnable = chat_prompt | llm
#        out = runnable.invoke({'text':text})    
#        return out.content
        question = """
        你是一位資深的基督教神學教授。给下面基督教牧師講道加小标题和段落的索引，不要返回講道内容。 回答须遵从以下JSON格式:
        ```json
        {
            "titles": [
                {
                    "title": "小标题",
                    "start_paragraph": 0
                },
                {
                    "title": "小标题",
                    "start_paragraph": 3
                }
            ]    
        }
        ```
        牧師講道:
        """
        messages = [{"role": "user", "content": f"{question}{text}"}]
        client = OpenAI(api_key="sk-db21da11c39642b592e6ebeeda50e87d", base_url="https://api.deepseek.com")
        response = client.chat.completions.create(
            model="deepseek-reasoner",
            messages=messages
        )        

        
        resp =  response.choices[0].message.content
        paras = self.markdown_to_json_to_dict(resp)
        return paras
    


    def parse_ai_response(self, ai_response:dict, script):

        sections = []        
        for key, sec in ai_response.items():
            if isinstance(sec, list) and len(sec) > 0 and 'title' in sec[0]:
                sections = sec
                break
        paras = []
        for i, sec in enumerate(sections):
            title = sec['title']
            idx_start = sec['start_paragraph']
            isEnd = False
            if i+1 < len(sections) and 'start_paragraph' in sections[i+1]:
                idx_end = sections[i+1]['start_paragraph'] - 1
            else: 
                idx_end = len(script) - 1    
                isEnd = True
            para = title + '\n' + '\n\n'.join( [ p['text'] for p in script[idx_start:idx_end+1] ] )
            paras.append(para)
            if isEnd:
                break

        return paras
    
    def get_source(self)->str:
        return "holylogos"
    
    def __init__(self) -> None:
        self.base_dir = os.getenv('base_dir')
    
    def get_content(self, meta:dict, script) -> list[str]:

        item_id = meta.get('item')        
        file_path =  os.path.join(self.base_dir, 'content_store', item_id + '.json')

        if os.path.isfile(file_path):
            with open(file_path, 'r') as file:
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
    text = """
我們談到這個問題，神為我們借着耶穌基督來預備挽回祭，我們上禮拜已經提過，我現在只是復習一下而已。神設立耶穌基督為我們的挽回祭，這個字希臘文是iλαστηριον這個字。iλαστηριον，我很快地講，上個禮拜講過的，所以我只是很快地提一下而已。
就是在以前一直把iλαστηριον翻成英文propitiation，我們中文只把它翻成挽回祭，所以沒有把不同的觀念講出來。我們說挽回祭代表什麼，很難了解。其實英文在二十世紀以前，都是翻成propitiation，“除去神的憤怒”。可是後來薛之道告訴我們說，propitiation是什麼，就是“除去神的憤怒”。
薛之道說，我們說神的憤怒，真的對不起，其實他完全錯誤。因為神對罪的憤怒，如果神對罪沒有憤怒，這個神不值得我們尊敬。因為神愛我們，而罪破壞我們跟神的關係，所以神就（憤怒）。其實神的憤怒，就是神對罪的反應，所以這個值得我們尊敬神。
可是呢，薛之道，他就說我們說神的憤怒是對神的不敬，其實他不知道神的憤怒，跟我們人的憤怒不一樣，因為是很重要，很重要的觀念。常常有人，沒有找聖經說，聖經講的意思到底是什麼。最近又有人跟我談，這些事情，他就說怎麼神會後悔，因為神既然什麼都知道，神怎麼會後悔。
我說“後悔”原來是什麼，因為後悔這個字，用在你身上的意思，跟用在神的身上不一樣。因為我們不知道，前面該做的事，將來發生什麼事，我們不知道，所以有時候我們做事會後悔。”後悔“，人的後悔包括一個regret，regret中文怎麼說，就是，遺憾，我們覺得很遺憾。
可是神既然知道，什麼會發生，神怎麼會有遺憾。所以你去了解，聖經跟你講得很清楚，你有沒有遺憾，是你自己主觀的反應。我不可能求你說，我求你這裡遺憾，不可能。你有沒有遺憾，那是你自己的反應。你看聖經從來沒有講過，我們可以請人遺憾。可是聖經好幾次講，告訴神說，關於我的事，請你後悔。
什麼意思，神的後悔是什麼，神的後悔沒有遺憾的因素，因為神是全知的，這要搞清楚。所以當我們用憤怒這個詞的時候，神的憤怒，跟我們的憤怒不一樣。所以現在知道犯了很大的錯，因為神的憤怒不是神的脾氣發作，神的憤怒就是神對罪的反應，神愛我們，所以神很氣罪，想辦法要把罪拿掉，這個就是propitiation的關鍵。
可是現在都搞錯了，而且我們知道，我們人生氣，比如說我對一個人生氣，我對不起我隨便抓，剛好這個弟兄坐在前面，所以我拿他做個例子。比如說我對他很生氣的話，我就希望他不會逃脫我對他生氣，我把他抓了，揍到我心甘，我心裡很滿意，我才會滿足。
可是呢，神的憤怒不是這樣，神的憤怒說，神就告訴你說，你犯罪了，我生氣，因為我要想辦法把罪拿掉，而且我替你想辦法，叫你能夠避免我的憤怒。跟人的憤怒完全不一樣，，只是那個字，憤怒那個字相同而已，意思完全不一樣。
現在犯了這個錯，所以這兩個，有人問我這個問題，他們沒有把這兩個觀念搞清楚。所以我稍微再很快的，跟各位提一下，其實這兩個觀念呢，是什麼，矛盾的。你不能說expiation是propitiation的一部分，錯，我根本沒有講那件事，錯，會有人把他聽成這樣，搞清楚，這兩個，不是expiation是propitiation，錯了，不要再錯了。
這個expression和propitiation是根本相反的觀念。所以啊，我們可以知道，propitiation是除去神的憤怒，神的憤怒，跟人的憤怒不一樣。神的憤怒，不是感情上的爆發，是神對罪的反應。很重要是什麼，神開路，使人脫離他憤怒的後果，是神的憤怒。
可是人的憤怒是什麼啊，不是開路，叫你避免我的憤怒，我是希望你逃不掉我的憤怒，叫我把你抓起來，把你侵犯到我心滿意足，完全不一樣。上禮拜我講過，因爲有人聽了以後，還問我，我說不是，你沒有把這個聽懂，很重要。
好了，因為我講過，我不能夠再撥時間。現在你看啊，很重要的觀念就在這裡。如果赦罪和神的屬性無關，那麼何必需要處理罪，使神可以人赦罪？這個完全是自相矛盾的想法，你說expiation，這是自相矛盾的想法，這個東西根本不能夠存在。
所以我既然已經講了，我就跳過去，我今天早上的時間，要講什麼，很重要，這個先告訴各位很重要。羅馬三章二十五節，非常重要的一節聖經，可是和合本翻錯，很重要，這個錯非常可怕。好了，什麼意思呢，我們仔細來看。
羅馬書三章二十五節，這個很重要。所以我把三種語言平列下來：ὃν προέθετο ὁ θεὸς ἱλαστήριον。我把它翻成英文，英文比較靠近希臘文的文法，所以我一個一個翻。ὃν就是Whom，προέθετο ὁ θεὸς就是God這個字，ὁ θεὸς就是God。Set forth προέθετο。 Whom God set forth as propitiation。 ἱλαστήριον，不是expedition，是 ἱλαστήριον， 是propitiation。
說的是什麼？把這個翻成中文，神，God就是神，設立他，設立誰？設立耶穌為挽回祭。下面非常重要，διὰ πίστεως 翻成英文 through faith，那中文呢？把它翻成因著信。然後呢，下面 ἐν τῷ αὐτοῦ αἵματι，英文就是翻 ἐν 就是in，τῷ是that，αὐτοῦ是his，αἵματι, blood，用他的血。這些聖經呢，非常的關鍵，非常的關鍵。
我查了一大堆中文翻譯，通通翻錯。最近剛出來的一本，叫做環球新約聖經的翻譯，有人送給我一本，叫我替他看。那我就查這幾個聖經，一查就錯。他那個環球最新翻譯的新約聖經，有的地方翻得比和合本好一點，有些進步沒錯，可是很多地方還是錯的。特別很重要的聖經，兩三個禮拜以前，人家送給我一本，是環球天道出版社出版的。
我在南加州教書的時候，原來他們的總經理是我的學生，我都忘記了。他碰見我說，我送給你一本我們最近剛出來的聖經，請你給我看看好不好。我說可以啊，他說給我，我說你帶回來。我看到他們有些地方有進步沒錯，可是呢，他告訴我說，這本聖經很正確。我說不知道，那我就去看看。我查了好幾個地方，還是錯的，這節聖經他也照樣錯，他會翻得跟和合本一樣，根本沒有改。
所以這很重要，所以我今天要好好地跟各位提一下，非常重要，你明白嗎？好。ἐν τῷ αὐτοῦ αἵματι, In his blood，不可能是信的受詞，這很重要，這非常的關鍵。為什麼呢？信的受詞是用εis，而不用ev。你信什麼，是πιστεύ εἰς。你相信耶穌，πιστεύ εἰς Ιησού ，Ιησού πιστεύ εἰς才對，不是ἐν。εἰς的意思就是into，不是ἐν。ἐν就是在裡面，into就是進入到那裡面。
所以我們相信神是什麼，我們相信耶穌，相信神是什麼？把我們自己，把我們的信心，完全投注到耶穌基督裡面，我們跟神的關係，投注在耶穌基督裡面，εἰς不一樣，從來不是用ἐν。還有很重要，信的受詞，你去找聖經，我認真查過，信的受詞是什麼？是主，是神，或者福音，而不是任何一個東西。
你去看聖經，聖經從來沒有叫我們相信什麼東西，相信什麼事，聖經只叫我們相信什麼？主，相信神，或者是福音，而已。查過，可以很容易查出來。我有一個software，我就把這一個”信“跟其他的名字連結在一起，查出來，它總是給我看， 整本聖經我查過，我都查過。我在Asbury教書的時候，1970年的時候，就查出來了。
從來，聖經叫我們信什麼，聖經只叫我們相信主，相信神，相信福音。聖經從來沒有教我們相信任何一件事，任何一個東西，包括耶穌的血。聖經從來沒有叫我們相信耶穌的血。你看看，我要給各位看，我這也是查出來，因為我的那個software幫助我很多時間，我就查。
幹什麼呢？我要給各位看一看。首先我們來看保羅書信，保羅書信裡面每次用到"év τῷ αἵματι αὐτοῦ", "靠著祂的血"，或者是"用祂的血"，豬指著什麼呢？豬指著神為我們預備救恩的方法。再講一次，"év τῷ αἵματι αὐτοῦ"，聖經從來沒有一次叫我們要相信耶穌的血，從來沒有，找不到。如果你找到的話，我請你吃大餐，因為我找不到。
我花了很多時間跟我學生講，在Asbury神學院上過我的課的學生，我在那裡教了三十幾年，所以，我的學生一兩萬人，經過我課堂上的學生。我跟他們講說，你們就是畢業之後，你們有時間去查。如果你查到有一個證據告訴我說，聖經要我們相信耶穌的血，你告訴我，我請你大餐。如果你還是學生的話，我就給你一張支票，不是很大的錢，至少一百塊，幫助你進來書店，幫助你做講學。直到現在沒有一個查到，沒有，就是沒有證據。
證據非常重要，所以只要我們信仰能夠建立在很重要的客觀的根基上。你再看，羅馬五章九节說，現在我們既靠著他的血，跟神有好關係，就更要藉 著他，免去神的忿怒。我們將來會再受神的憤怒的影響，哥林多前书第十一章25节。
這杯是用我的血，in my blood，所立的新約。你們每逢喝的時候，要如此行，為的是紀念我。以弗所一章七集，我們藉著这愛子的血，through his blood，得蒙救赎，過犯得以赦免，乃是照他豐富的恩典。再看，以弗所第二章十三集，你們從前遠離神的人，如今卻在基督耶穌里，靠著他的血，已經可以親近神。
所以，in the blood，in his blood， through his blood，每一次都講到神為我們預備救恩的方法。還有，我就把保羅书信裡面所有的，in his blood，有關through his blood，通通找出來。 歌罗西一章二十节一樣，既然藉著他在十字架上所流的血，成就了和平， 便藉著他叫萬有，無論是地上的，天上的，都與自己和好了。
所以我給你這個證據，得到什麼？ 很重要的結論，在保羅書信中， ἐν τῷ αἵματι, in the blood, 或 διὰ τοῦ αἵματος, through the blood，都指著神預備救恩的方法。而沒有一次清楚地是信的受词。在保羅書信中，一次都沒有，找不到。你不相信的話，你花時間去找，你找找這個證據之後，你會非常有很大的滿足感。
我真的找到證據，教我能夠真的把重要的聖經，我還沒有看到一個中文的聖經把這段聖經翻對，我還沒有看到。所以保羅書信裡面，in the blood，或是through the blood，都指著神預備救恩的方法。沒有一次是信的受词，換句話說，保羅書信從來沒有教我們相信耶穌的血，沒有。
再給各位看，不但是保羅書信，就整本新約聖經。我現在要給各位看證據從哪裡出來，就從新約聖經裡面，不是保羅書信，保羅書信以外的新約聖經，給你看證據。一樣，在新約聖經裡面，保羅書信以外的新約聖經裡面，In his blood，或者By his blood，因着他的血，通通是用來描寫神為我們預備救恩的方法，而沒有一次教我們來相信耶穌的血。
你看，馬太二十二章二十八节，這是耶穌設立聖餐的時候所講的話。而且，其實他的平行经文，在馬可14章24节，在路加福音22章20节，耶穌說什麼？因為這是我立约的血，rò alpui uuti the diathènç，My blood，All the cunning，我立约的血，為多人流出來，使罪得赦的。
再看，彼得前書一章十八节到十九节，知道你們得贖，脫去你們祖宗所傳流虛妄的行為，不是憑著能壞的金銀等物，乃是憑著基督的寶血，如同無瑕 疵無玷污的羔羊之血。所以，耶穌的寶血幹什麼呢？就是叫我們得贖。為我們預備救恩。 所以你看，寶血是什麼，我們得救，神為我們預備，我們可以得救的方法，耶穌的寶血，聖經沒有叫相信耶穌的寶血。
约翰1書1章7节，我們若在光明中行, 如同神在光明中, 就彼此相交, 有fellowsbip. 他兒子耶穌的血(the blood of Jesus), 他兒子，耶穌基督的血，也洗淨我們一切的罪。所以耶穌的血幹什麼，要洁净我們的罪，把我們的罪除掉，是神拯救的方法，從來沒有叫我們相信耶穌的血。
启一章五节，他愛我們，耶穌愛我們，用自己的血 ἐν τῷ αἵματί σου，因his own blood 使我們脫離罪惡，很重要。所以耶穌基督的血，不但是使神能夠赦免我們的罪，還有使我們脫離罪的捆綁。启五章九节，他們唱新歌說, 你配拿書卷, 配揭開七印; 因為你曾被殺, (ἐν τῷ αἵματί σου) in hisblood 用自己的血， 從各族各方,各民各國中買了人來。 從各族各方，各名各國中，叫他們歸神。
所以耶穌基督的血做什麼? 使我們能夠跟神有好關係。所以結論是什麼? 很重要. 在新的聖經中，in the blood，或者是through the blood，ἐν αἰ αἰῶνι，就是in the blood，或者διὰ τοῦ αἰῶνος，through the blood，都指神預備救贖，救人的方法。而没有一次清楚地是信的受词
整個新的聖經裡面，現在我給各位看，很重要的東西。這個東西，幾十年前，我在Asbury 教书的時候，我就跟學生講這個東西，很麻煩。講一次兩次他們沒有聽懂，所以感謝神，有一天，想說我要怎麼把這個觀念給學生看。所以我想一想，這個圖，我是祷告神，我要幫助學生了解這個東西。要用什麼方法教學生，一看就知道。
我想想說，神忽然就給我idea，画個圖給他們看。這個圖就那個时候来的。1， 2， 3。我用這個來解釋，我講一次學生聽懂。1是什麼？1是神設立耶穌為挽回祭。2是什麼？2是因著信，3是什麼？ 3是用耶穌的血。很重要。1神設立耶穌為挽回祭。2.因著信， 3.用耶穌的血
現在，很重要。2跟3不能連在一起。2是什麼？因著信， 用耶穌的血 in the blood of Jesus，你看英文，更容易了解我在講什麼。你這裡說什麼？ in the blood of Jesus，1是 God set forth Jesus as propitiation， 英文. 關鍵在這裡，因著信 through faith，用耶穌的血， 英文是 in the blood of Jesus。
2跟3連在一起，變成 belief was through faith in the blood of Jesus。2，3連在一起的话，英文很容易把它念成 through the belief in。 英文是用in the blood of Jesus， so belief in the blood of Jesus。英文文法看起來很容易這麼看，可是我說，剛才我給你看的證據，不可能。2跟3不能連在一起，2，3不能連在一起，就是你 you cannot say，你不能說 belief faith in the blood of Jesus。
聖經從來沒有這個觀念 in the blood of Jesus，是什麼?聖經所講的信是什麼？你只有信神，只有信主，只有信福音，信 the object，信的受词，只有主，只有神，只有福音，沒有其他。所以聖經從來，沒有要我們，相信耶穌的血，所以，2跟3不能連在一起，2跟3不能連在一起。
可是，你看，1跟3連在一起，1是什麼？神設了耶穌为挽回祭，用耶穌的血，神設了耶穌为挽回祭，神設了耶穌为挽回祭，用耶穌的血。所以1跟3連在一起，2跟3不能連在一起，所以什麼？唯一的可能，1跟2連在一起。1跟2連在一起。
聽懂了?邏輯，非常重要，非常的關鍵。中國教會從來沒有提過這件事，所以中國人看這東西，看不懂. 非常關鍵。再講一次，所以聖經從來沒有說，叫我們信耶穌的血，Faith in the blood of Jesus，Belief in the blood of Jesus，從來沒有. 所以2跟3不能連在一起，不能說你要信，耶穌的血，2跟3不能連在一起。
可是什麼？2跟3不能連在一起，1跟3連在一起，神設了耶穌为挽回祭，用耶穌的血，神設了耶穌为挽回祭，用耶穌的血。所以結果是什麼？2不能跟3連在一起，1跟3連在一起，所以，2只能跟1連在一起。
我這麼一講，學生聽懂了。這圖畫，其實，我最初沒有這個圖畫的時候，我就跟學生看這個東西。我現在跟你講，你不會馬上抓到。他們是將來要做牧師的這些人，我跟他們講真實的，其實我在這個講完之後，你很困難馬上就抓到我在講什麼什麼。
你看，這裡，用他的血，跟神設立他为挽回祭，連在一起。這個第3，這是第1，1跟3連在一起，所以中間的，也必須和因著信連在一起。神設立他为挽回祭，因著信連在一起。
那我沒有畫这個圖以前，我就跟學生講這個東西。學生抓不到頭緒。王教授你在講什麼東西？我那天我記得，有一次我在學生那裡，跟學生講，我可能講了五六次，還是沒有聽懂。我說好啦，我今天晚上回去想想看，有什麼辦法。
所以那天下午我回家，到底我要怎麼樣幫助學生了解，想一想，我忽然想到這件事，就想到這個東西，把這個圖寫出來。我說你先不要看，我現在就跟你講那個東西，你看我這個圖，他們看就知道，1跟3連在一起，2跟3不能連在一起，所以1跟2應該連在一起。
那一天他們聽懂了。1就是什麼?神設立耶穌为挽回祭，2是因著信，3是什麼，用耶穌的血，1跟3連在一起，2跟3不能連在一起. 因為，2跟3連在一起的話，就是神要我們信耶穌的血，可是聖經從來沒有講過這個，聖經從來沒有要求我們信耶穌的血，所以那個已經把那個都打掉。
可是華人教會通通沒有看到這一點. 所以直到現在，錯到現在，這很重要，等一下你就知道，為什麼我花這麼多時間講這個東西，非常重要。所以你看，因此信，與神設立耶穌为挽回祭有關，所以2跟1連在一起。所以2是信，1是神設立耶穌的为挽回祭，有關係的。
我問各位，這是什麼東西？神設立耶穌为挽回祭，因著信，藉著耶穌的血。所以這個信，是誰的信？是人的信，還是神的信？很重要，信與神設立耶穌为挽回祭有關係，信與神設立耶穌为挽回祭有關係。
所以這個信，是神的信，還是人的信？當然是神的信，看到沒有？神的信实，使祂為我們設立耶穌基督为挽回祭，而且，因著神的信实，出於神的信实，出於神的信实，祂為我們設立耶穌为挽回祭，用什麼？用耶穌的血。
所以信，就是2，用耶穌的血，都是來描寫神為我們設立耶穌为挽回祭。因此這個信不是人的信，是神的信。和合本把它翻譯成人的信，不一樣。那是神的信实。所以這個信，是神的信，不是人的信，非常關鍵，非常重要，這個信是神的，不是人的。所以你看，非常重要。
你看，既然如此，可以看到，羅馬三章二十一節跟二十二節，也一樣。但如今神的義在律法以外已經顯明出來了，有律法和先知為証。那是什麼意思? 律法和先知為證，律法的象徵整個旧约，就一直在強調，神是信实可靠的。
而且神的信实，包括什麼? 如果你記得，我忘記我在講台上是不是在這裡講過。不過我講約翰福音書的時候，我講過很重要。當真理，信实，用在神身上的時候，有一個很重要的觀念，一旦神是信实了，所以他要拯救。神的信实，包括神要拯救，還有，神的信实，包括他的應許可靠。
所以在這裡，你看，和合本也是犯錯，就是神的義，因信耶穌基督。不是，這裡說什麼? （dia pisteos insou Christou ？？」因耶穌基督的信实，或者，藉着耶穌基督的信实，加给一切相信的人。其實這裡是，準確的翻譯是，临到一切繼續相信的人並沒有分別。所以三章二十一節，二十二節，也一樣。
我們和合本通通，把保羅的意思翻错。其實這裡，保羅很強調神的信实，他既然應許，差耶穌基督來，為我們預备救恩，神是信实可靠。可是呢，和合本通通把神的信实拿掉，把它翻成，因信耶穌基督。其實不是，這個不是因信耶穌基督，乃是因耶穌基督信实，加给一切相信的，或者說，临到一切繼續相信的人，並沒有分別。
我要給各位看一件事，按照中文的翻譯，按照中文的翻譯，羅馬書三章二十一節二十二節，把神的信实拿掉，沒有談到神的信实，談到人相信兩次，因信耶穌基督，加给一切繼續相信的人，所以講到，基督徒，人，信耶穌兩次，談兩次，可是沒有提到神的信实。很重要。
可是按照保罗所写的原文，神的信实，加上人繼續相信，神的救恩完成。一方面需要神的信实。神本身，祂是信实可靠的。我跟各位提過，我讲約翰福音書的時候，提過的，也很少人知道這件事。所以我查出來。這是什麼東西呢? 當信实用在神身上的時候，就包括神為我們預備救恩，神的信实，包括祂為我們預備救恩。
所以神的信实很重要。可是和合本通通把它拿掉。所以他们只把聖經的一部分翻譯出來，把很重要的部分没有翻出來。所以念和合本，念這個中文翻譯的聖經，對不起，就變成跛脚。跛脚你聽得懂嗎? 就是不完整，你不完整，一個正常的走路會很方便，你跛脚是什麼，你只得到一部分的東西，你只懂一半，一半都沒有。所以很困擾。
再给你看，羅馬三章三节說什麼?即便不信的，這又何妨呢? 難道他們的不信，就废掉神的信实嗎? 所以你看，神的信实，你不能夠把它 ignore， 忽视，不要看它，把它拿掉，错了。神的信实非常重要，當人家談到救恩的時候，神的信实非常重要。雖然，人對神不信实，可是神還是信实。你不能废掉神的信实，保罗很強調神的信实。
如果，如果，如果你知道神是信实。你就可以知道，他所講的話可靠。你知道神是信实，信实的神，一定要拯救。所以神要拯救我們，是什麼，是跟他的性格相稱，所以他一定要拯救，他一定要幫助。
如果你知道神是這樣的話，所以為什麼缘故我們要知道，藉著聖經來認識神的本性。如果你認識神的本性的話，你知道他是可靠的，他的應許一定要成就。所以你不要擔心，你既然不擔心，你既然不擔心，你就老得很慢。
因為今天心理學家这么講，我們今天為什麼工作效果很低，為什麼我們老得很快，原因是什麼？就是因為我們的anxiety。中文怎麼說anxiety? 焦慮，憂慮。anxiety是什麼，我對將來我不知道怎麼樣，我不可靠，所以一天到晚，焦慮這樣，憂慮那样，不一樣。
可是如果你知道，神是信实的，神一定要救我，只要我不离弃他，神一定要救我，神的應許神的一定要做到。所以，你没有焦慮。像我的兒子，他小時候，像在我那三歲，三歲的孫子裡面，他知道公公要來，我每次去，他都跟我講說公公，next time，回來看你。
他現在有什麼東西，他喜歡吃的東西，就留著我去的時候再給我，公公。他知道公公可靠，他知道可靠，他就不要擔心。
。所以信实可靠，神的信实可靠非常重要。你知道神的信实可靠的話，你一切的焦慮就沒有了。
神的目的就是要我們過最健康的生活。所以我知道神可靠，所以我一直不擔心。我最近發生了一件事，有人問我說你有什麼感想，我說沒有什麼感想，因為我知道神在掌權。我知道神是我的主，祂可靠，我一點都不擔心。
大家告訴我說你奇怪啊，這樣發生這麼嚴重的事，一點都不關心。我说關心干什么？我關心也沒用，我焦慮也沒用。因為我的父親，他的本性就是幫助我。他是我們的父親，他既然是我們的父親，父親的本性就是幫助我們。那你認識這個父親，你擔心幹嘛？
所以我一點都不擔心。大家說如果我的話，我大哭。我說我不哭。不是因為我是沒有情感，我有情感，可是什麼？這件事對我講起來很小事，對我一點受伤都沒有。我說神，有更好的方法來使用我，感謝主。這樣，你再看，羅馬三章五節一樣。
我且照着人的常话說，我們的不義若顯出神的义來，我們可以怎麼說呢？神降怒是祂不義嗎？不是。你看，羅馬三章七節又是強調神的信实。若神的信实，因我的虚谎，願法顯出祂的榮耀，為什麼我還受审判，好像罪人啊？
其實，神的真實，神的可靠，都是保罗在羅馬書一再的強調。可是和合本沒有把這一個三章二十五節，很重要的那一個信，其實那裡所講的神的信， 神的信实，为我们预备救恩。和合本把它翻錯，就是人的信，不是。當然人的信很重要，你如果沒有信，來接受，你得不到。可是什麼呢？可是保羅說，神的信实非常可靠。
所以你看羅馬書一章第二節明講，這福音是神從前，藉著眾先知，在聖经上所應許，神的信实可靠。神既然應許，你怕什么。對不對？好了，所以你看，羅馬書三章25， 26節。神设立耶穌為挽回祭，怎麼說呢？因著信，和合本翻譯因著人的信，不是。因著神的信实，用耶穌的血，要顯明神的義。
神的義是什麼？義的观念是什么？義的观念是好關係，以及促進好關係的行為。顯明神的義，這件事來顯明，神要促進好關係，為我們預備救恩，使我們藉着耶穌基督，我們跟神的關係能建立起來。顯明神的義，因為他用忍耐的心，寬容的先时所犯的罪，使人知道他自己为义。什麼？使靠耶穌的信实的人成為義。這很重要。
又強調靠著耶穌的信实，又強調耶穌基督的信实，圣经強調神的信实。神拯救我們，神恩待我們，是跟他的本性相似，跟他的本性相稱。你如果知道這一點，你就不要擔心了，你擔心幹嘛？
你看，我要給各位看，羅馬三章二十五节，要顯明神的義，強調神的信实，而沒有提人的信。羅馬三章二十六节，要顯明神的義，來強調耶穌的信实，而沒有提人的信。好，這個非常的重要，羅馬三章二十五句的證據，非常的強。
我把這個觀念，跟很多學生講，跟很多學者講。尤其是華人，華人神學院的新的教授，我跟他們講，他們說，和合本怎麼翻成這樣子？我說你去問他，不是我翻的。我給他看這個證據之後，他們就没话讲。羅馬三章二十五節，是什麼？是神，怎麼說呢？是神，设立耶穌為挽回祭，因著神的信实。好。所以這個信实很重要。羅馬三章二十五節很重要。 羅馬三章二十五節很清楚強調神的信实。
可是什麼？因此呢，羅馬三章二十五節的證據，羅馬三章二十二節，是什麼東西？各位看，你看，你看，怎麼樣？“????" 好。through the faith Jesus Christ, And to all who繼續相信的人。所以羅馬三章二十五節，所講的是什麼？神的义，因著耶穌基督的信实，臨到一切繼續相信的人。
所以羅馬3章22節，中文又是翻错，非常重要。所以怎麼樣？你看，羅馬三章二十五節，羅馬三章二十五節，強調神的信实。羅馬三章二十六節，強調耶穌的信实，還有什麼？人靠著耶穌的信实。所以我們要靠著耶穌的信实。羅馬三章二十六節，談到耶穌基督的信实，人繼續信。
再給你看，加拉太第二章十六節，既知道人稱義，其實應該是人成为義。成为義是什麼？因為他們從前把義當作神宣告你没有罪，在法庭上宣告你没有罪。其實義我就跟各位講過了，義主要的觀念是跟神的好關係。所以其實翻成稱義就錯了，應該翻成成為義。
既然知道人成為義跟神的好關係，不是因行律法，乃因什麼？中文又把它翻錯了，因信耶穌基督。加拿大二章十六節，其實不是，因耶穌基督的信实，強調因耶穌基督的信实。但我們信耶穌基督，是我們因信耶穌基督成为義，不因律法來成为義，因為凡有血气的沒有一個人因行律法成為義。
所以律法跟神的信实，跟耶穌基督的信实，是什麼？冲突。律法跟耶穌基督的信实冲突。如果你接受耶穌基督的信实，我下禮拜要跟你們講這件事情，因為到底是什麼東西？下禮拜也是很重要的觀念。那個觀念如果沒有搞清楚，罗马书看不懂。很重要。
那個東西，是我博士論文。我博士論文發表之後，很多人開始接受我講那套，因為我有證據，所以他們就不停的接受。我下禮拜跟你們講那件事，可能要花一個鐘頭。
那你看，律法跟基督的信实是兩個不同的東西，衝突的東西。再看，加2：21，我不會掉神的恩。义若是藉着律法得的，基督就是徒然死。你如果說藉着律法，要跟神建立好關係，那基督何必死？如果能夠藉着律法跟神建立好關係，基督死幹嘛？基督的死就徒然沒有用。
那你看，律法跟基督的死是什麼？衝突！ 而且剛才我們也看到，基督的信实，基督的信实跟律法衝突。好，那現在我們來看，羅馬三章二十五節，神设立耶穌做挽回祭，是憑著耶穌的血，藉着人的信。那是中文翻錯，原文應該是什麼？神设立耶穌为挽回祭，因著神的信实，藉着耶穌的血要顯明神的義。
所以，神的信实，也就是耶穌的死。所以我們可以看到，你只看羅馬書，羅馬書三章二十五節，耶穌基督的信实，還有人的继续信，神的救恩部分，耶穌基督的信实，人的继续信，三章二十五節講到神的信实，三章二十六節談到耶穌的信实，人靠耶穌的信实。所以神救我們兩方面，神做的部分，我們的部分。神部分做完了，我們要藉着信来接受，神的救恩才能夠成全。
那你再看，加拉太書第二章十六節，耶穌基督的信实跟人的信，加拉太書三章二十六節談到耶穌基督的信实·，人继续信。所以你看，神设立耶穌为挽回祭是什麼？憑著神的信实，神的信实跟耶穌基督的信实一樣，但只有继续相信的人才能得到他的好處。
神设立耶穌为挽回祭，是憑神的信实，也就是耶穌基督的信实，但只有繼續相信的人，才能得到他的好處。如果你沒有繼續信他的話，耶穌為你預備的救恩就對你身上沒有效果。
這一點，我為什麼花這麼多時間講這些，因為非常的重要，非常的重要。告訴你什麼意思。罗马一章16-17节是整个罗马书的主题，他把罗马书的重点写出来，那里有一句话，這義就是神的義，神為我們預備救恩，使我們跟神建立好關係，這個義是什麼，本於信，以至於信，中文這麼翻的，沒有人看懂，我那時候跟各位提過，搞錯了。
非常重要，”ἐκ πίστεως εἰς πίστιν,？“是什麼？本於或者出於神的信实，临到繼續相信的人。所以神要跟你建立好關係，需要兩個因素，是什麼？出於神的信实。神的信实，出於神的信实，临到信的人，我記得我那時候告訴各位說，中文范畴出于信,以至於信，很多英語的翻譯也沒有把這個翻對，他們說，everything, from beginning to end, all faith，錯了。不是說，是什麼呢，ἐκ πίστεως εἰς πίστιν，是本於或者出於神的信使，临到信的人，我那時候跟我講說，這是這個意思，可是我那時候也沒有時間給各位看證據，我說那時候要給你看證據的話，就花一個鐘頭時間，像今天一樣，花了一個鐘頭時間。
所以我說，我講這句話是有根基的，我時間到我就給你看，果然時間到，這個時候，三章二十五講非常的重要，可是和合本就把它翻错，所以我們就沒有看到，這種很重要的信息，你完全不了解，不知道什麼信实，所以神是信实的，神既然是信实的，你就知道祂可靠的，你既然知道他可靠，你焦虑幹什么，那问题就出现了。
所以為什麼我說神學很重要，神學什麼意思呢，我們知道神，藉着聖經，我們了解神的性格，神是信实可靠的，神的信实包括他願意拯救人，神的信实包括應許，他一定要成全。所以如果你知道這件事，知道神是這麼一位，你一生不管你過什麼情況，你就一點焦虑都沒有，我現在遇到一些困難，神藉这個困難要教導我們，教我們學習更依靠神。
你知道這件事的話，你碰到困難的時候，很清楚，你要學什麼，幫助我趕快學好，你就不會受苦，對不對，很重要。好了，我們一起禱告。
非常感謝你給我們今天這個時間來看，羅馬書三章二十五節所強調的，因為你的信，因為你的信使，你設立耶穌基督，為我們的挽回基督，用耶穌的血，你為我們預備這個完整的救恩，非常感謝你，求你幫助我們更清楚地認識你是信使有可靠的神。
你既然是信使有可靠的神，你的本性就是要拯救我們脫離罪惡，你就是要幫助我們，造就我們，使我們成為你兒子，教我們說話，行為，能夠像你兒子一樣，與我們的身份相似，求你幫助我們的弟兄妹妹，能夠一直追隨，教我們能夠一起，能夠活出我们的身份。
    """
    

    se = SermonExtractor()

    resp = se.split(text)



    ai_response = '好的,我根據您的講道內容,為每個段落加上小標題如下:\n\n[0] 教會裡面的傳統教導\n[1] 保羅處理哥林多教會關於吃祭偶像之物的問題\n[2] 要從聖經來看保羅的教導\n[3-4] 保羅在哥林多前書的教導(一)\n[5] 保羅在哥林多前書的教導(二) \n[6-7] 解釋"良心軟弱就污穢了"的意思\n[8-10] 保羅關於在市場上買祭偶像之物的教導\n[11-14] 總結保羅關於吃祭偶像之物的教導\n[15-20] 一個弟兄因不明白聖經教導而受虧損的例子\n[21-22] 再次總結保羅關於吃祭偶像之物的教導\n[23-29] 使徒行傳15章的背景\n[30-32] 雅各在使徒行傳15章的提議\n[33-34] 使徒們寫信吩咐外邦信徒的四點要求\n[35-38] 使徒們寫信的對象:安提阿、敘利亞、基利家的外邦信徒\n[39-42] 從利未記看神對猶太人的特別要求\n[43-44] 再次強調使徒們信的對象和原因\n[45-47] 使徒行傳15章和哥林多前書的教導對比\n[48-52] 聖經原則在不同處境中的應用\n[53-58] 如何從聖經細則中找出聖經原則\n[59-64] 女人蒙頭的聖經原則和今日應用\n[65-69] 哥林多前書8章的教導\n[70-73] 不叫人跌倒的重要性\n[74-79] 約翰一書3章的教導\n[80-81] 哥林多前書10章的教導\n[82-83] 保羅憑良心行事為人\n[84] 保羅勸勉提摩太要存無虧的良心\n[85-87] 良心的定義和功用\n[88-90] 要使良心的標準與聖經的標準一致\n[91] 呼籲弟兄姐妹明白聖經真理,使用良心來生活\n\n講道的起始段落是[0]。'
    sections = se.parse_ai_response(ai_response, se.sermons[0]['script'])
    print(sections)
    pass

#    se.get_embedded_content()
    
