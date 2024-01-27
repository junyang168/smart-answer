from flask import Flask, render_template, session
from flask_restful import Resource, Api, reqparse


#from chat_agent import handleChat
import os
import json


app = Flask(__name__, static_url_path='/static')
api = Api(app)

app.secret_key = 'BAD_SECRET_KEY'


import sys
import os 
from dotenv import load_dotenv

# Get the current script's directory
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the parent directory by going one level up
parent_dir = os.path.dirname(current_dir)
# Add the parent directory to sys.path
sys.path.append(parent_dir)


from smart_answer_service.ghostwriter import GhostwriterService
from smart_answer_core import logger

class Ghostwriter(Resource):
    def post(self):
        parser = reqparse.RequestParser()  # initialize
        parser.add_argument('get_history', required=False)  # add args
        parser.add_argument('get_prompt_template', required=False)  # add args        
        parser.add_argument('user_message', required=True)  # add args
        parser.add_argument('uid', required=False)
        args = parser.parse_args()  # parse arguments to dictionary
        user_message = args['user_message']    
        uid = args['uid'] 
        get_history = args['get_history']
        get_prompt_template = args['get_prompt_template']
        new_session = False

        if not uid:
            uid = session.get("uid")
            if not uid:
                new_session = True
                import uuid
                myuuid = uuid.uuid4()
                uid = "gw-" + str(myuuid)
                session["uid"] = uid
                
        try:
            gw = GhostwriterService(uid)
            if get_history == 'True':
                if new_session:
                    gw.get_question(user_message)
                ai_message =  gw.get_chat_history()
            else:
                ai_message = gw.get_question(user_message)
            ai_message = ai_message.replace("\n","<br/>")
            resp = {"response": ai_message }
            if get_prompt_template == 'True':
                resp["prompt_template"] = gw.get_prompt_template()
            return resp , 200
        except Exception as e:
            logger.exception("unable to answer question " + user_message )
            return {"error": "Ghostwriter Internal Error  " + str(e)}, 400
        
class GhostwriterTemplate(Resource):
    def post(self):
        parser = reqparse.RequestParser()  # initialize
        parser.add_argument('template', required=True)  # add args
        args = parser.parse_args()  # parse arguments to dictionary
        prompt_template = args['template']    
                
        try:
            gw = GhostwriterService(None)
            gw.set_prompt_template(prompt_template)
            
            return {"response":"success"} , 200
        except Exception as e:
            logger.exception("unable to answer question " + prompt_template )
            return {"error": "Ghostwriter Internal Error  " + str(e)}, 400
        

api.add_resource(Ghostwriter, '/ghostwriter')
api.add_resource(GhostwriterTemplate, '/ghostwriter/prompt_template')

@app.route("/")
def index():
   return render_template('index.html')

@app.route("/ghostwriter")
def ghost_writer():
   return render_template('ghostwriter.html')


if __name__ == '__main__':
    current_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(current_dir, '.env')
    load_dotenv(dotenv_path)
    pt = os.environ.get("PORT") 
    use_ssl = os.environ.get("SSL")
    if use_ssl == 'True':
        app.run(host='0.0.0.0', debug=True, port=pt,ssl_context=('/home/admin/fullchain.pem','/home/admin/privkey.pem'))
    else:
        app.run(host='0.0.0.0',  debug=True, port=pt)
