
from smart_answer_junyang168.tool_selector import tool_selector
import smart_answer_junyang168.util as util

class SmartAnswer:
    prompt_template = """Answer the question at the end using the following pieces of context. If there is not enough information in the context to answer the question, just say that you don't know, don't try to make up an answer.
    Format response in markdown.
    {context}

    Question: {question}
    Helpful Answer:"""


    def __init__(self, tools) -> None:
        self.selector = tool_selector(tools)


    def __get_answer(self,question, context, tool ):
        prompt_template = tool.get_answer_prompt_template(self.prompt_template, context)
        return util.ask_llm(prompt_template, output_type=None, question = question, context=context )

    def __get_content_reference(self, result):
        if isinstance(result, str) :
            return result, None
        else:
            return result.get('content'), result.get('reference')

    def get_smart_answer(self, question, sid = None,isFollowUp = False, context_only = False):         
        if not question:
          return ("", None, None, None )

#        expanded, expanded_question =  util.expand_acronyms(question)
#        if expanded:
#            question = expanded_question

#        chatMemory = ChatMemory(sid) 
#        question = chatMemory.add_question(question, isFollowUp )      

        tool, args = self.selector.select_tool(question)
        answer = None
        if tool:
            result = tool.retrieve(args, question)
            context_content, reference = self.__get_content_reference(result)
            question_prefix = result.get("prefix") 
            if not question_prefix: 
                question_prefix = ""
            answer =self.__get_answer( question_prefix + question, result, tool)
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

