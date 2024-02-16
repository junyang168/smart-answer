import sys
import os 
from dotenv import load_dotenv
import datetime 
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from smart_answer_core.LLM.LLMWrapper import LLMWrapper
def validate(question, answer, ground_true):
    validation_template = """
    Validate if the answer to the question below is consistent with the ground truth below. Respond with a confidence score between 0 and 10 ONLY. Do NOT user your own knowledge. 
    Question: {question}
    Answer: {answer}
    Ground Truth: {ground_truth}
    Confidence Score:
    """
    llm = LLMWrapper(provider = "Langchain", model='openai/gpt-4')

    inputs = {"question": question, "answer": answer, "ground_truth": ground_true}

    confidence = llm.askLLM(validation_template, inputs, None)
    return confidence




from smart_answer_service import smart_answer_service
import os
import pandas as pd

df = pd.read_excel(current_dir + "/GenAI_Test_Re.xlsx")

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
    confidence_score = validate(question,answer,ground_truth)
    df.loc[index,'Validation'] = confidence_score
    print(index, question, answer, tool_name, confidence_score)
    if index % 5 == 0:
        df.to_excel(current_dir + "/GenAI_Test_Result.xlsx", index=False)





