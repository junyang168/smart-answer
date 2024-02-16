
from smart_answer_core.tool_selector import tool_selector
import smart_answer_core.util as util
from smart_answer_core.expand_acronyms import acconym_expansion

class SmartAnswer:
    prompt_template = """Answer the question at the end using the following pieces of context. 
    If there is not enough information in the context to answer the question, explain why the question can not be answered with the context. don't try to make up an answer.
    Format response in markdown.
    {context}

    Question: {question}
    Helpful Answer:"""


    def __init__(self, tools) -> None:
        self.selector = tool_selector(tools)
        self.fallback_tool = [t for t in tools if t.is_fallback_tool()][0]
        pass


    def __get_answer(self,question, context, tool ):
        prompt_template = tool.get_answer_prompt_template(self.prompt_template, context)
        return util.ask_llm(prompt_template, output_type=None, question = question, context=context )

    def __get_content_reference(self, result):
        if not result:
            return None, None
        
        if isinstance(result, str) :
            return result, None
        else:
            return result.get('content'), result.get('reference')

    def get_smart_answer(self, question, sid = None,isFollowUp = False, context_only = False):         
        if not question:
          return ("", None, None, None )
        
        ae = acconym_expansion()
        expanded, expanded_question = ae.expand_acronyms(question)
        if expanded:
            question = expanded_question

#        chatMemory = ChatMemory(sid) 
#        question = chatMemory.add_question(question, isFollowUp )      

        tool, args = self.selector.select_tool(question)
        answer = None
        if tool:
            result = tool.retrieve(args, question)
            if not result:
                result = self.fallback_tool.retrieve(args, question)

            context_content, reference = self.__get_content_reference(result)

            if isinstance(result, str):
                answer = result
            else:
                question_prefix = ""
                if result:
                    question_prefix = result.get("prefix") 
                answer = self.__get_answer( question_prefix + question, context_content, tool)
#        if answer:
#            chatMemory.add_answer(answer)

        return (answer, context_content, tool.name, reference )

if __name__ == '__main__':


    questions = [
        "How many days are left until ESXi version 5.1 reaches the end of technical guidance?"
]


    sa = SmartAnswer()

    for q in questions:
#        answer = get_smart_answer(q, sid, True)
        answer = sa.get_smart_answer(q)
        print(answer[0])

