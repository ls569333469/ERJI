from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from engine.config import settings

class Base(DeclarativeBase):
    pass

engine = create_engine(settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
Session = sessionmaker(bind=engine, expire_on_commit=False)

def session_scope():
    with Session() as session:
        yield session

