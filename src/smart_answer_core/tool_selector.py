
from smart_answer_core.base_tool import base_tool
import smart_answer_core.util as util

from langchain.pydantic_v1 import BaseModel, Field, validator
from typing import List
from smart_answer_core.LLMWrapper import LLMConfig

class tool_selector:

    prompt_template = """
        Choose the best tool listed below to answer user’s question. Respond with NA if none of the following tools can answer the question.
        {tool_names}

        RESPONSE FORMAT INSTRUCTIONS
        ----------------------------
        {format_instructions}

        {tool_few_shots}

        User Question: {question}
        Answer 
    """

    def __init__(self, tools : List[base_tool], llm_cfg : LLMConfig ) -> None:
        self.tools = tools
        self.fallback_tool = [t for t in tools if t.is_fallback_tool()][0]
        self.llm_cfg = llm_cfg


    def _create_prompt(self,tools):            
            tool_names = "\n".join(
                [f"> {tool.name}: {tool.description}" for tool in tools]
            )

            ex_idx = 1
            examples = []
            for i, tool in  enumerate(tools):
                tool_ex = tool.get_few_shots()
                examples.extend(  [f"Example {ex_idx + j}:\n {ex.get_output(tool)}" for j, ex in enumerate( tool_ex )] )
                ex_idx += len(tool_ex)

            few_shots = '\n'.join(examples)

            return  self.prompt_template, {"tool_names":tool_names, "tool_few_shots":few_shots  }

    def _get_tool_input(self, tools, resp):
        ts = [t  for t in tools if t.name.lower() == resp.get('tool','').lower() ]
        if len(ts) > 0: 
            tool = ts[0]
            return tool, resp.get('tool_input')
        else:
            return self.get_fallback_tool(), None
     

    def select_tool(self, question :str):

        if len(self.tools) == 1:
            return self.tools[0], None

        chat_prompt, inputs = self._create_prompt(self.tools)

        inputs["question"] = question
        resp =  util.ask_llm(self.llm_cfg, chat_prompt, format='Json' , **inputs)
        return self._get_tool_input(self.tools, resp)
    
    def get_fallback_tool(self):
        return self.fallback_tool


if __name__ == '__main__':
    import sample_tools as st
    tools = [st.LifeCycleTool(), st.InterOperabilityTool(), st.KB_DocTool()]
    selector = tool_selector(tools)
    questions = [ "How many days are left until ESXi version 5.1 reaches the end of technical guidance?",
                 "Which version of NSX is not compatible with Vmware HCX?",
                 "How do I enable retreat mode for a cluster in vcenter?" ]
    for question in questions:
        tool, tool_input = selector.select_tool(question)
        print(question)
        print( f"tool: {tool.name} args:{tool_input}" )







