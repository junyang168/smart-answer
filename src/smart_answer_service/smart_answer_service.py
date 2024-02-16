import sys
import os 
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)


from  smart_answer_core.base_tool import base_tool
from smart_answer_core.smart_answer import SmartAnswer 
from smart_answer_core.tool_example import tool_example

from smart_answer_core.tools.lifecycle import LifeCycleTool
from smart_answer_core.tools.kb_doc import KB_DocTool
from tools.interoperability import InterOperabilityTool
from tools.configMax import ConfigMaxTool
import os

class smart_answer_service:
        def __init__(self) -> None:
                CONNECTION_STRING = os.environ["CONNECTION_STRING"]
                self.tools = [LifeCycleTool(CONNECTION_STRING), InterOperabilityTool(), KB_DocTool(CONNECTION_STRING), ConfigMaxTool()]

        def get_answer(self, question: str, sid = None, isFollowup = False ):
                sa = SmartAnswer(self.tools)
                return sa.get_smart_answer(question,sid )


if __name__ == '__main__':
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        dotenv_path = os.path.join(parent_dir, '/app/.env')
        load_dotenv(dotenv_path)

        questions = [ 
                "what is the Limit applicable to PVSCSI only. Any combination of disk or VMDirectPath SCSI target."
        #        "When Gemstone/S 64-bit 2.4 will release ?"
        #    "what is the maximum number of dfw(Distributed Firewall) rules in nsx-t 4.1? "
        #        "till when technical guidance active for product AppDefense Plugin 2.0 for Platinum Edition ?",
        #        "Which version of NSX is not compatible with Vmware HCX?",
        #        "How do I configure vGPUs on esxi 8?", 
        #        "How many Virtual CPUs per virtual machine (Virtual SMP) for vcenter 8.0"
        #            "How many virtual CPUs can I have in a virtual machine in vcenter 8.0"
        #        "FSDisk: 301: Issue of delete blocks failed"
                ]
        for question in questions:
                sa = smart_answer_service()
                answer = sa.get_answer(question)
                print(answer[0])
