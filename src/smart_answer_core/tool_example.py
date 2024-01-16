import json

class tool_example:
    question = ""
    tool_input = ""
    def __init__(self, question, tool_input) -> None:
        self.question = question
        self.tool_input = tool_input
    def get_output(self, tool):
        d =  {
            "tool": tool.name,
            "tool_input":self.tool_input      
            }
        return f"User Question:{self.question}\n" + json.dumps(d)


