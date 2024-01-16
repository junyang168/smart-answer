from  smart_answer_junyang168.base_tool import base_tool
from smart_answer_junyang168.smart_answer import SmartAnswer 
from smart_answer_junyang168.tool_example import tool_example

from smart_answer_junyang168.tools.lifecycle import LifeCycleTool
from smart_answer_junyang168.tools.interoperability import InterOperabilityTool

CONNECTION_STRING="postgresql://postgres:airocks$123@192.168.242.24:5432/postgres"

tools = [LifeCycleTool(CONNECTION_STRING), InterOperabilityTool(CONNECTION_STRING)]
sa = SmartAnswer(tools)
questions = [ 
#    "How many days are left until ESXi version 5.1 reaches the end of technical guidance?",
                "Which version of NSX is not compatible with Vmware HCX?",
#                "How do I enable retreat mode for a cluster in vcenter?" 
             ]
for question in questions:
    answer = sa.get_smart_answer(question)
    print(answer[0])
