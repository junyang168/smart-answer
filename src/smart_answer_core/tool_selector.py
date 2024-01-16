
from smart_answer_junyang168.base_tool import base_tool
import smart_answer_junyang168.util as util

from langchain.pydantic_v1 import BaseModel, Field, validator

class ToolSelectorResponse(BaseModel):
    tool: str = Field(description="Name of the tool ")
    tool_input: str = Field(description="input parameter to the tool")

class tool_selector:

    prompt_template = """
        Choose the best tool listed below to answer user’s question.
        {tool_names}

        RESPONSE FORMAT INSTRUCTIONS
        ----------------------------
        {format_instructions}

        {tool_few_shots}

        User Question: {question}
        Answer 
    """

    def __init__(self, tools) -> None:
        self.tools = tools

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
        ts = [t  for t in tools if t.name == resp.tool ]
        if len(ts) > 0: 
            tool = ts[0]
        return tool, resp.tool_input
     

    def select_tool(self, question :str):

        chat_prompt, inputs = self._create_prompt(self.tools)

        inputs["question"] = question
        resp =  util.ask_llm(chat_prompt, ToolSelectorResponse, **inputs)
        return self._get_tool_input(self.tools, resp)
 
    def select_next_tool(self, tool, context_content):
        return None


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







