import sys
import os 
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)


from smart_answer_core.base_tool import base_tool
from smart_answer_core.smart_answer import SmartAnswer 
from smart_answer_core.tool_example import tool_example

from smart_answer_core.tools.lifecycle import LifeCycleTool
from smart_answer_core.tools.kb_doc import KB_DocTool
from tools.interoperability import InterOperabilityTool
from tools.configMax import ConfigMaxTool
from tools.demo_tool import DemoTool
import os


from typing import List

from fastapi import Depends, FastAPI
from pydantic import BaseModel
import smart_answer_service as sas
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from smart_answer_core.base_tool import Reference

class SmartAnswerResponse(BaseModel):
    answer: str
    references: List[Reference] = None
    tool: str = None

class SmartAnswerRequest(BaseModel):
    org_id:str = None
    question: str
    sid:str = None
    is_followup:bool = False


@app.post("/get_answer", response_model=SmartAnswerResponse)
def get_answer(request: SmartAnswerRequest):
        CONNECTION_STRING = os.environ["CONNECTION_STRING"]
        if request.org_id == 'test':
            tools = [DemoTool()]
        else:
            tools = [LifeCycleTool(CONNECTION_STRING), InterOperabilityTool(), KB_DocTool(CONNECTION_STRING), ConfigMaxTool()]
        sa = SmartAnswer(tools)
        answer, context_content, tool, references  = sa.get_smart_answer(request.question, sid=request.sid, isFollowUp=request.is_followup)
        resp = SmartAnswerResponse(answer=answer, references=references)
        return resp


import uvicorn
if __name__ == "__main__":

      #  uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))

        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        dotenv_path = os.path.join(parent_dir, '/app/.env')
        load_dotenv(dotenv_path)

        questions = [ 
        #      "我的服务器宕机了，怎么办"
                "重启也没用"
        #      "What are the steps to configure GPUs on esxi 8?"
        #      "How to deploy vRSLCM to a VCF 3.x WLD that is using VLAN backed networks?"
        #      "when will ESXi 7 go out of support"
        #        "If I want to setup a MSCS with physical nodes should I use cluster in a box?"
        #        "How should I react to an abusive customer?"
        #        "What is the latest BIOS version for the Dell PowerEdge R740?"
        #        "what all advanced configuration options  for Esxi ?"
        #        "what is in-kernel distributed logical routing system for NSX"
        #        "what all supported releases for ESXi for model ASMB-816"
        #        "What is RDS Host Maximums"
        #        "what is the Limit applicable to PVSCSI only. Any combination of disk or VMDirectPath SCSI target."
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
                req = SmartAnswerRequest(question=question, org_id='test', sid='sss1', is_followup=True)
                resp = get_answer(req)
                print(resp)
