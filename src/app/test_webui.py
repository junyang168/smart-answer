from langchain import LLMChain
from langchain_community.chat_models import ChatOpenAI
import langchain.chains.retrieval_qa.prompt as qa
from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder
)
import langchain.agents.conversational_chat.prompt as ap

def _create_prompt(user_prompt_template, inputs):            
        input_variables = list(inputs.keys())
        messages = [
            SystemMessagePromptTemplate.from_template(ap.PREFIX),
            HumanMessagePromptTemplate.from_template(user_prompt_template)
        ]
        return ChatPromptTemplate(input_variables=input_variables, messages=messages)

few_shot_examples = """
        Example 1:
 User Question:When will vSphere 7 go out of support
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere 7"}
Example 2:
 User Question:When will vSphere 7 be released
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere 7"}
Example 3:
 User Question:What versions of vShpere are still supported
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere"}
Example 4:
 User Question:What versions of vShpere are released
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere"}
Example 5:
 User Question:Is vSAN  compatible with vCenter?
{"tool": "VMWare Product Compatibility", "tool_input": "vSAN, vCenter"}
Example 6:
 User Question:How to configure vGPU in ESXi?
{"tool": "VMWare Knowledge Base", "tool_input": "ESXi, configure vGPU"}
Example 7:
 User Question:what is limit of vCPU per RDS host for Horizon 2306
{"tool": "VMware Product Configuration Limits", "tool_input": "Horizon 2306, limit of vCPU per RDS host"}
"""

format_instructions= """
        The output should be formatted as a JSON instance that conforms to the JSON schema below.

As an example, for the schema {"properties": {"foo": {"title": "Foo", "description": "a list of strings", "type": "array", "items": {"type": "string"}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema. The object {"properties": {"foo": ["bar", "baz"]}} is not well-formatted.

Here is the output schema:
```
{"properties": {"tool": {"title": "Tool", "description": "Name of the tool ", "type": "string"}, "tool_input": {"title": "Tool Input", "description": "input para
"""

user_prompt_template="""
 Choose the best tool listed below to answer user’s question.
        > VMWare production version and life cycle dates:  use this tool to understand support dates, general availability date and end of technical guidance date of VMware product versions
        The input to this tool should be  the VMWare product release. Use comma delimited string if question is about multiple releases.
    
> VMWare Product Compatibility: 
        use this tool to understand compatibiilty or interoperability between VMWare products.  
        The input to this tool should be a comma separated list of string of length two, representing the two product releases you wanto understand compatibility with.
        For example, 
            1. `Aria 1.0,ESXi 5.0` would be the input if you wanted to know whether VMware Aria 1.0  can run on VMware ESXi 5.0. 
            2. `Aria,ESXi 5.0` would be the input if you wanted to know the versions of Aria that support VMware ESXi 5.0. 
    
> VMWare Knowledge Base: This is the default tool to understand any VMWare product related issues and questions other tools can't handle. 
      Do not use this tool if other tools can answer the question. Use this tool if other tool returns 'Unable to get data'
      The input to this tool should be a comma separated list of string of length two, representing VMware product release and the topics of the question.
      
> VMware Product Configuration Limits: use this tool to get the recommended maximums or limits for VMware product configurations or settings. 
        The input to this tool should be a comma separated list of string of length three, representing VMWare product release and the metrics(e.g. CPU per VM) for the limit.
        Here are some sample quesions:
        How much RAM can I run on a VM?
        Can I run 40GB RAM on a VM?

        RESPONSE FORMAT INSTRUCTIONS
        ----------------------------
        {format_instructions}

        {tool_few_shots}

        User Question: {question}
        Answer 

"""
inputs =  {"question":"How many days are left until ESXi version 5.1 reaches the end of technical guidance?",
           "tool_few_shots":few_shot_examples,
           "format_instructions":format_instructions}

chat_prompt = _create_prompt(user_prompt_template,inputs)

model = "teknium_OpenHermes-2.5-Mistral-7B"
api_url = "http://127.0.0.1:5000/v1"

llm = ChatOpenAI(temperature=0,model_name= model , openai_api_key = "na", openai_api_base= api_url, streaming=False, max_tokens=1000)

chain = LLMChain(llm=llm, prompt = chat_prompt)

print( chain.run(inputs) )
exit()


import requests

url = "http://127.0.0.1:5000/v1/chat/completions"

headers = {
    "Content-Type": "application/json"
}

history = []

user_message = """
 Choose the best tool listed below to answer user’s question.
        > VMWare production version and life cycle dates:  use this tool to understand support dates, general availability date and end of technical guidance date of VMware product versions
        The input to this tool should be  the VMWare product release. Use comma delimited string if question is about multiple releases.
    
> VMWare Product Compatibility: 
        use this tool to understand compatibiilty or interoperability between VMWare products.  
        The input to this tool should be a comma separated list of string of length two, representing the two product releases you wanto understand compatibility with.
        For example, 
            1. `Aria 1.0,ESXi 5.0` would be the input if you wanted to know whether VMware Aria 1.0  can run on VMware ESXi 5.0. 
            2. `Aria,ESXi 5.0` would be the input if you wanted to know the versions of Aria that support VMware ESXi 5.0. 
    
> VMWare Knowledge Base: This is the default tool to understand any VMWare product related issues and questions other tools can't handle. 
      Do not use this tool if other tools can answer the question. Use this tool if other tool returns 'Unable to get data'
      The input to this tool should be a comma separated list of string of length two, representing VMware product release and the topics of the question.
      
> VMware Product Configuration Limits: use this tool to get the recommended maximums or limits for VMware product configurations or settings. 
        The input to this tool should be a comma separated list of string of length three, representing VMWare product release and the metrics(e.g. CPU per VM) for the limit.
        Here are some sample quesions:
        How much RAM can I run on a VM?
        Can I run 40GB RAM on a VM?

        RESPONSE FORMAT INSTRUCTIONS
        ----------------------------
        The output should be formatted as a JSON instance that conforms to the JSON schema below.

As an example, for the schema {"properties": {"foo": {"title": "Foo", "description": "a list of strings", "type": "array", "items": {"type": "string"}}}, "required": ["foo"]}
the object {"foo": ["bar", "baz"]} is a well-formatted instance of the schema. The object {"properties": {"foo": ["bar", "baz"]}} is not well-formatted.

Here is the output schema:
```
{"properties": {"tool": {"title": "Tool", "description": "Name of the tool ", "type": "string"}, "tool_input": {"title": "Tool Input", "description": "input para

        Example 1:
 User Question:When will vSphere 7 go out of support
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere 7"}
Example 2:
 User Question:When will vSphere 7 be released
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere 7"}
Example 3:
 User Question:What versions of vShpere are still supported
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere"}
Example 4:
 User Question:What versions of vShpere are released
{"tool": "VMWare production version and life cycle dates", "tool_input": "vSphere"}
Example 5:
 User Question:Is vSAN  compatible with vCenter?
{"tool": "VMWare Product Compatibility", "tool_input": "vSAN, vCenter"}
Example 6:
 User Question:How to configure vGPU in ESXi?
{"tool": "VMWare Knowledge Base", "tool_input": "ESXi, configure vGPU"}
Example 7:
 User Question:what is limit of vCPU per RDS host for Horizon 2306
{"tool": "VMware Product Configuration Limits", "tool_input": "Horizon 2306, limit of vCPU per RDS host"}

        User Question:  How many days are left until ESXi version 5.1 reaches the end of technical guidance?
        Answer

"""
history.append({"role": "user", "content": user_message})
data = {
    "mode": "chat",
    "character": "Example",
    "messages": history
}

response = requests.post(url, headers=headers, json=data, verify=False)
assistant_message = response.json()['choices'][0]['message']['content']
history.append({"role": "assistant", "content": assistant_message})
print(assistant_message)