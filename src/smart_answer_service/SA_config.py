import sys
import os 

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)



import os
from smart_answer_core.LLMWrapper import LLMConfig
from smart_answer_core.base_tool import base_tool
import json
from smart_answer_core.tools.lifecycle import LifeCycleTool
from smart_answer_core.tools.kb_doc import KB_DocTool
from tools.interoperability import InterOperabilityTool
from tools.configMax import ConfigMaxTool
from tools.demo_tool import DemoTool

class SmartAnswer_Config:
    tools : list[base_tool]
    llm_config : LLMConfig

def load_config() -> dict[str, LLMConfig]: 
    file_name = os.environ['CONFIG_FILE'] 
    with open(file_name) as cfg_file:
        cfg_data = json.load(cfg_file)
        llm_config = {}
        for cfg_name, cfg in cfg_data.items():
            config = SmartAnswer_Config()
            config.llm_config = LLMConfig(api_url=cfg['LLM_API_URL'],api_key=cfg['LLM_API_KEY'],model= cfg['LLM_MODEL'])
            llm_config[cfg_name] = config
            if cfg_name == 'test':
                config.tools =  [DemoTool()]
            else:
                CONNECTION_STRING = os.environ["CONNECTION_STRING"]
                config.tools = [LifeCycleTool(CONNECTION_STRING), InterOperabilityTool(), KB_DocTool(CONNECTION_STRING,config.llm_config), ConfigMaxTool()]

        return llm_config

configuration : dict[str, SmartAnswer_Config] = load_config()

