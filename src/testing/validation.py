import sys
import os 
from dotenv import load_dotenv
import datetime 

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from smart_answer_service import smart_answer_service
import os
from smart_answer_core import util
import pandas as pd

df = pd.read_excel(current_dir + "/GenAI_Test_Result.xlsx")

restart = input('Start?[Y/N]')
if restart == 'Y':
    for index, row in df.iterrows():
        df.loc[index,'Run'] = ''
    df.to_excel(current_dir + "/GenAI_Test_Result.xlsx", index=False)


for index, row in df.iterrows():
    has_run = row["Run"]
    if has_run == 'DONE':
        continue
    question = row["Question"]
    ground_truth = row["Ground Truth"]
    if isinstance(ground_truth, datetime.datetime) :
        ground_truth = ground_truth. strftime("%m/%d/%Y")
        df.loc[index,'Ground Truth'] = ground_truth
    sa = smart_answer_service.smart_answer_service()
    answer, context, tool_name, reference =  sa.get_answer(question)
    df.loc[index,'answer'] = answer
    df.loc[index,'intention'] = tool_name
    df.loc[index,'Run'] = 'DONE'
    print(index, question, answer, tool_name)
    if index % 5 == 0:
        df.to_excel(current_dir + "/GenAI_Test_Result.xlsx", index=False)





validation_template = """
Validate if the answer to the question below is consistent with the ground truth below. Respond with a confidence score between 0 and 10 ONLY. Do NOT user your own knowledge. 
Question: {question}
Answer: {answer}
Ground Truth: {gound_truth}
Confidence Score:
"""


confidence = util.ask_llm(validation_template,output_type=None, question = question, answer = answer, gound_truth=ground_truth)

os.environ["OPENAI_API_KEY"] = "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"


