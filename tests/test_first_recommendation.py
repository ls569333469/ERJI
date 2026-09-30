from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from engine.models import SignalSnapshot
from engine.pd2.outcomes import first_recommendation


def test_history_is_filtered_before_loading_snapshots():
    engine=create_engine('sqlite://')
    SignalSnapshot.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([SignalSnapshot(id=str(i),plan_id='plan',as_of=i,
            payload={'action':{'code':('trial' if i==80 else 'participate' if i==90 else 'watch')},'evidence':'x'*1000})
            for i in range(100)])
        session.commit()
    with Session(engine) as session:
        loaded=[]
        event.listen(session,'loaded_as_persistent',lambda s,row:loaded.append(row.id))
        first=first_recommendation(session,'plan')
        assert first.id=='80'
        assert loaded==['80']
        assert first_recommendation(session,'missing') is None
        assert loaded==['80']
    engine.dispose()
