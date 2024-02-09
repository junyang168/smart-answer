from home import app
import os
from dotenv import load_dotenv

if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(current_dir, '.env')
    load_dotenv(dotenv_path)
 
    app.run()

