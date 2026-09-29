"""Local isolated verification only; never log credential values."""
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.dev-deps'))
from engine.config import Settings
from sqlalchemy.engine import make_url
source=Settings(_env_file='D:/Axiangmu/erji/.env')
os.environ['ERJI_DATABASE_URL']=make_url(source.database_url).update_query_dict({'options':'-csearch_path=erji_pd2_20260924'}).render_as_string(hide_password=False)
os.environ['ERJI_CMC_KEY_FILE']='F:/key/CMC.txt'
os.environ['ERJI_BYKARANTELI_KEY_FILE']='F:/key/bykaranteli.txt'
