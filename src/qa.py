import os
os.environ["LLM"]  = "GPT"
#os.environ["OPENAI_API_KEY"] = "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"
os.environ["OPENAI_API_KEY"] = "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"
os.environ["EMBEDDING_MODEL"] = "BGE"
os.environ["USE_GPU"] = "False"



from  smart_answer_core.base_tool import base_tool
from smart_answer_core.smart_answer import SmartAnswer 
from smart_answer_core.tool_example import tool_example

from smart_answer_core.tools.lifecycle import LifeCycleTool
from smart_answer_core.tools.interoperability import InterOperabilityTool
from smart_answer_core.tools.kb_doc import KB_DocTool



CONNECTION_STRING="postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"

tools = [LifeCycleTool(CONNECTION_STRING), InterOperabilityTool(CONNECTION_STRING), KB_DocTool(CONNECTION_STRING)]
sa = SmartAnswer(tools)
questions = [ 
#    "How many days are left until ESXi version 5.1 reaches the end of technical guidance?",
#                "Which version of NSX is not compatible with Vmware HCX?",
                "How do I configure vGPUs on esxi 8?" 
             ]
for question in questions:
    answer = sa.get_smart_answer(question)
    print(answer[0])
