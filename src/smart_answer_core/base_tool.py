class base_tool:
    name = ""
    description = ""


    def get_few_shots(self):
        return []

    def retrieve(self, args, question):
        return None

    def get_answer_prompt_template(self, prompt_template, context):
        return prompt_template

