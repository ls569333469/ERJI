import os,uvicorn
os.environ['ERJI_WEB_ORIGIN']='http://127.0.0.1:18930'
os.environ['ERJI_API_ORIGIN']='http://127.0.0.1:18931'
uvicorn.run('services.api.main:app',host='127.0.0.1',port=18931,log_level='warning')
