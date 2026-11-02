import os
from app import create_app
if __name__=='__main__':
    create_app().run(host='127.0.0.1',port=int(os.environ.get('MI2_PORT','8091')),debug=False)
